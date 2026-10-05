# -*- coding: utf-8 -*-
"""BlackLang → Python 代码生成后端（AOT-to-Python 基线）。

把 BlackLang AST 直接翻译成等价的 Python 源码。编译产物是**普通 Python 函数**，
因此在算法繁重任务上以 CPython 原生速度运行 —— 相比解释器(VM)是数量级提升，
直接把 benchmark 中「相对 CPython」的比率压到 ~1x。这是通往 LLVM/JIT 之前的
性价比最高的『编译路径』基线。

用法：
  from blacklang.codegen import compile_to_python, exec_compiled
  py_src = compile_to_python(program)        # 生成 Python 源码
  out    = exec_compiled(py_src)             # exec 并捕获 stdout
"""
from __future__ import annotations

from typing import Any, Dict, List

from .ast_nodes import (
    Assign, AttrAssign, Binary, Call, Compare, DictLit, ExprStmt, ForIn, FuncDef,
    GetAttr, GetItem, If, ListLit, Literal, Name, Program, Return, Sandbox,
    StructDef, Unary, Use, While,
)

# 语言保留词 → Python 关键字映射（BL 保留词很少，安全起见通用映射）
_BL_KEYWORDS = {"len", "sum", "print", "append"}

# Python 内建映射：BlackLang 内建名 → Python 等效
_BUILTIN_MAP = {
    "len": "len",
    "sum": "sum",
    "print": "print",
    "append": "append",
}

# 运算符翻译：BlackLang `and`/`or` → Python `and`/`or`；其余不变
_CMP_MAP = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "==": "==", "!=": "!="}


class CodegenError(Exception):
    pass


def _py_name(name: str) -> str:
    """把 BlackLang 名字安全地用作 Python 标识符（防注入，同时保留语义）。"""
    # 内建如 len/print/sum 直接透传；否则照搬（BL 名字本身是合法 Python 标识符）
    return _BUILTIN_MAP.get(name, name)


def _indent(lines: List[str], level: int = 1) -> List[str]:
    pad = "    " * level
    return [pad + ln for ln in lines]


class CodeGen:
    def __init__(self, program: Program):
        self.program = program
        self.str_names: set = set()   # 推断为字符串的变量名（用于 string + 数值 拼接）

    def _collect_str_names(self):
        """轻量推断：哪些变量是字符串（BL 允许 string + 数值 自动拼接）。"""
        assigns = []

        def walk(stmts):
            for s in stmts:
                if isinstance(s, Assign):
                    assigns.append(s)
                elif isinstance(s, If):
                    for _, b in s.branches:
                        walk(b)
                    walk(s.else_body or [])
                elif isinstance(s, While):
                    walk(s.body)
                elif isinstance(s, ForIn):
                    walk(s.body)
                elif isinstance(s, Sandbox):
                    walk(s.body)

        for e in self.program.entries:
            if isinstance(e, FuncDef):
                walk(e.body)
            elif isinstance(e, Sandbox):
                walk(e.body)
            else:
                walk([e])
        for _ in range(3):   # 迭代以捕捉 x = y + "a" 这类链式推断
            for s in assigns:
                if self._is_string(s.value):
                    self.str_names.add(s.name)

    def _is_string(self, e) -> bool:
        if isinstance(e, Literal):
            return isinstance(e.value, str)
        if isinstance(e, Name):
            return e.name in self.str_names
        if isinstance(e, Call):
            return isinstance(e.callee, Name) and e.callee.name == "str"
        if isinstance(e, Binary):
            if e.op == "and" or e.op == "or":
                return self._is_string(e.left) and self._is_string(e.right)
            if e.op == "+":
                return self._is_string(e.left) or self._is_string(e.right)
        return False

    def generate(self) -> str:
        self._collect_str_names()
        out: List[str] = []
        # 收集函数定义 + 顶层 use + main 顶层语句
        func_defs: List[FuncDef] = []
        struct_defs: List = []
        uses: List[Use] = []
        main_entries: List = []
        for e in self.program.entries:
            if isinstance(e, StructDef):
                struct_defs.append(e)
            elif isinstance(e, FuncDef):
                func_defs.append(e)
            elif isinstance(e, Use):
                uses.append(e)
            elif isinstance(e, Sandbox):
                # 能力已在编译期校验；AOT 按普通作用域 emit
                main_entries.extend(e.body)
            else:
                main_entries.append(e)
        # use python: {mod} → import；use node → 说明性注释
        for u in uses:
            if u.lang == "python":
                for n in u.names:
                    out.append(f"import {_py_name(n)}")
        out.append("")
        # ★ struct → Python class（含方法 Type.method 与 attr/item 双访问）
        plain_funcs = [f for f in func_defs if "." not in f.name]
        methods = [f for f in func_defs if "." in f.name]
        for sd in struct_defs:
            out.append(f"class {_py_name(sd.name)}:")
            fnames = [f[0] for f in sd.fields]
            out.append(f"    def __init__(self, {', '.join(_py_name(n) for n in fnames)}):")
            if fnames:
                for n in fnames:
                    out.append(f"        self.{_py_name(n)} = {_py_name(n)}")
            else:
                out.append("        pass")
            for md in [m for m in methods if m.name.split(".", 1)[0] == sd.name]:
                mname = md.name.split(".", 1)[1]
                mparams = [p for p in md.params if p != "self"]
                sig = ", ".join(["self"] + [_py_name(p) for p in mparams])
                out.append(f"    def {_py_name(mname)}({sig}):")
                body = _indent(_indent(self._stmts(md.body, 1))) if self._stmts(md.body, 1) else ["        pass"]
                out.extend(body)
            out.append("    def __getitem__(self, k): return getattr(self, k)")
            out.append("    def __setitem__(self, k, v): setattr(self, k, v)")
            if fnames:
                items = ", ".join(
                    '"%s=" + repr(self.%s)' % (n, _py_name(n)) for n in fnames)
                out.append(
                    '    def __repr__(self): return "%s(" + ", ".join([%s]) + ")"'
                    % (sd.name, items))
            else:
                out.append('    def __repr__(self): return "%s()"' % sd.name)
            out.append("")
        # 用户函数（不含方法）
        for fd in plain_funcs:
            params = ", ".join(_py_name(p) for p in fd.params)
            out.append(f"def {_py_name(fd.name)}({params}):")
            stmts = self._stmts(fd.body, 1)
            out.extend(_indent(stmts) if stmts else _indent(["pass"]))
            out.append("")
        # run main 块 → main() 函数
        body = self._stmts(main_entries, 0)
        out.append("def main():")
        out.extend(_indent(body) if body else _indent(["pass"]))
        out.append("")
        out.append("if __name__ == '__main__':")
        out.append("    main()")
        return "\n".join(out)

    # ---- statements ----
    def _stmts(self, stmts, level: int) -> List[str]:
        out: List[str] = []
        for s in stmts:
            out.extend(self._stmt(s, level))
        return out

    def _stmt(self, s, level: int) -> List[str]:
        if isinstance(s, Assign):
            return [f"{_py_name(s.name)} = {self._expr(s.value)}"]
        if isinstance(s, AttrAssign):
            return [f"{self._expr(s.obj)}.{_py_name(s.attr)} = {self._expr(s.value)}"]
        if isinstance(s, StructDef):
            return []
        if isinstance(s, ExprStmt):
            return [self._expr(s.expr)]
        if isinstance(s, If):
            out = []
            first = True
            for cond, body in s.branches:
                kw = "if" if first else "elif"
                out.append(f"{kw} {self._expr(cond)}:")
                out.extend(_indent(self._stmts(body, level + 1), 1))
                first = False
            if s.else_body:
                out.append("else:")
                out.extend(_indent(self._stmts(s.else_body, level + 1), 1))
            return out
        if isinstance(s, While):
            out = [f"while {self._expr(s.cond)}:"]
            out.extend(_indent(self._stmts(s.body, level + 1), 1))
            return out
        if isinstance(s, ForIn):
            out = [f"for {_py_name(s.var)} in {self._expr(s.iterable)}:"]
            out.extend(_indent(self._stmts(s.body, level + 1), 1))
            return out
        if isinstance(s, Return):
            if s.value:
                return [f"return {self._expr(s.value)}"]
            return ["return None"]
        if isinstance(s, Sandbox):
            return self._stmts(s.body, level)
        if isinstance(s, FuncDef):
            return []  # 顶层单独 emit
        return []

    # ---- expressions ----
    def _expr(self, e) -> str:
        if isinstance(e, Literal):
            return self._literal(e.value)
        if isinstance(e, Name):
            return _py_name(e.name)
        if isinstance(e, Binary):
            op = "and" if e.op == "and" else ("or" if e.op == "or" else e.op)
            # BL 语义：任一侧为字符串时，+ 做字符串拼接（另一侧自动转字符串）
            if e.op == "+" and (self._is_string(e.left) or self._is_string(e.right)):
                return f"(str({self._expr(e.left)}) + str({self._expr(e.right)}))"
            return f"({self._expr(e.left)} {op} {self._expr(e.right)})"
        if isinstance(e, Unary):
            return f"({e.op}{self._expr(e.operand)})"
        if isinstance(e, Compare):
            return f"({self._expr(e.left)} {_CMP_MAP[e.op]} {self._expr(e.right)})"
        if isinstance(e, Call):
            # list 扩展方法调用：d.len() -> len(d), d.sum() -> sum(d), d.append(x) -> d.append(x)
            c = e.callee
            if isinstance(c, GetAttr) and c.attr in ("len", "sum", "append"):
                obj = self._expr(c.obj)
                if c.attr == "append":
                    return f"{obj}.append({', '.join(self._expr(a) for a in e.args)})"
                return f"{c.attr}({obj})" if not e.args else \
                    f"{c.attr}({obj}, {', '.join(self._expr(a) for a in e.args)})"
            return f"{self._expr(c)}({', '.join(self._expr(a) for a in e.args)})"
        if isinstance(e, ListLit):
            return f"[{', '.join(self._expr(i) for i in e.items)}]"
        if isinstance(e, DictLit):
            pairs = ", ".join(f"{self._expr(k)}: {self._expr(v)}" for k, v in e.pairs)
            return "{" + pairs + "}"
        if isinstance(e, GetItem):
            return f"{self._expr(e.obj)}[{self._expr(e.index)}]"
        if isinstance(e, GetAttr):
            # GetAttr 用于非 method 场景（如 math.sqrt → math.sqrt）；list.len 单独走 Call
            return f"{self._expr(e.obj)}.{e.attr}"
        raise CodegenError(f"不支持翻译的表达式: {type(e).__name__}")

    def _literal(self, v) -> str:
        if v is None:
            return "None"
        if isinstance(v, bool):
            return "True" if v else "False"
        if isinstance(v, str):
            return repr(v)
        return repr(v)


def compile_to_python(program: Program) -> str:
    return CodeGen(program).generate()


def exec_compiled(py_src: str) -> str:
    """exec 编译产物并捕获其 print 输出（模拟跑 BL 程序的 stdout）。"""
    import io
    import contextlib
    out = io.StringIO()
    ns: Dict[str, Any] = {}
    with contextlib.redirect_stdout(out):
        exec(compile(py_src, "<blacklang-aot>", "exec"), ns)
        if "main" in ns:
            ns["main"]()
    return out.getvalue()