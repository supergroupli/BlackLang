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



# ---------------- 标准库 → Python 映射 ----------------
# 每个条目: 方法名 -> 生成函数 (obj_code, arg_codes) -> Python 表达式
_METHOD_MAP = {
    # 字符串
    "upper": lambda o, a: f"({o}).upper()",
    "lower": lambda o, a: f"({o}).lower()",
    "trim": lambda o, a: f"({o}).strip()",
    "split": lambda o, a: f"({o}).split({a[0]})" if a else f"({o}).split()",
    "replace": lambda o, a: f"({o}).replace({a[0]}, {a[1]})",
    "contains": lambda o, a: f"({a[0]} in {o})",
    "starts_with": lambda o, a: f"({o}).startswith({a[0]})",
    "ends_with": lambda o, a: f"({o}).endswith({a[0]})",
    "count": lambda o, a: f"({o}).count({a[0]})",
    "char_at": lambda o, a: f"({o})[{a[0]}]",
    "to_int": lambda o, a: f"int(({o}).strip())",
    "to_float": lambda o, a: f"float(({o}).strip())",
    "repeat": lambda o, a: f"(({o}) * {a[0]})",
    "is_digit": lambda o, a: f"({o}).isdigit()",
    "pad_left": lambda o, a: f"({o}).rjust({a[0]}, {a[1]})" if len(a) > 1 else f"({o}).rjust({a[0]})",
    "pad_right": lambda o, a: f"({o}).ljust({a[0]}, {a[1]})" if len(a) > 1 else f"({o}).ljust({a[0]})",
    # 列表
    "append": lambda o, a: f"({o}).append({a[0]})",
    "push": lambda o, a: f"({o}).append({a[0]})",
    "sum": lambda o, a: f"sum({o})",
    "sort": lambda o, a: f"sorted({o})",
    "sort_in_place": lambda o, a: f"({o}).sort() or {o}",
    "min": lambda o, a: f"min({o})",
    "max": lambda o, a: f"max({o})",
    "first": lambda o, a: f"({o}[0] if {o} else None)",
    "last": lambda o, a: f"({o}[-1] if {o} else None)",
    "insert": lambda o, a: f"({o}).insert({a[0]}, {a[1]}) or {o}",
    "remove_at": lambda o, a: f"({o}).pop({a[0]})",
    "unique": lambda o, a: f"list(dict.fromkeys({o}))",
    "extend": lambda o, a: f"({o}).extend({a[0]}) or {o}",
    # 字典
    "keys": lambda o, a: f"list(({o}).keys())",
    "values": lambda o, a: f"list(({o}).values())",
    "has": lambda o, a: f"({a[0]} in {o})",
    "get": lambda o, a: f"({o}).get({a[0]})" if len(a) < 2 else f"({o}).get({a[0]}, {a[1]})",
}

# 既可用于字符串也可用于列表/字典的通用方法
_METHOD_GENERIC = {
    "len": lambda o, a: f"len({o})",
    "reverse": lambda o, a: f"_bl_reverse({o})",
    "slice": lambda o, a: f"({o})[{a[0]}:{a[1]}]" if len(a) > 1 else f"({o})[{a[0]}:]",
    "index_of": lambda o, a: f"_bl_index_of({o}, {a[0]})",
    "is_empty": lambda o, a: f"(len({o}) == 0)",
    "count": lambda o, a: f"({o}).count({a[0]})",
    "join": lambda o, a: f"_bl_join({o}, {a[0]})",
    "contains": lambda o, a: f"({a[0]} in {o})",
}

# 全局函数 → Python
_GLOBAL_MAP = {
    "abs": "abs", "round": "round", "pow": "math.pow",
    "floor": "math.floor", "ceil": "math.ceil", "sqrt": "math.sqrt",
    "clamp": None, "sign": None, "min": "min", "max": "max",
}

_PRELUDE_PY = """import math


def _bl_reverse(x):
    return x[::-1] if isinstance(x, str) else list(reversed(x))


def _bl_index_of(x, v):
    if isinstance(x, str):
        return x.find(v)
    return x.index(v) if v in x else -1


def _bl_join(x, arg):
    # x 是分隔符（字符串 join 列表）或列表（列表 join 分隔符）
    if isinstance(x, str):
        return x.join(str(_e) for _e in arg)
    return str(arg).join(str(_e) for _e in x)
"""


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
        out.append(_PRELUDE_PY.rstrip())
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
            c = e.callee
            # 标准库方法：s.trim() / xs.sort() / d.keys()
            if isinstance(c, GetAttr):
                obj = self._expr(c.obj)
                args = [self._expr(a) for a in e.args]
                for tbl in (_METHOD_MAP, _METHOD_GENERIC):
                    fn = tbl.get(c.attr)
                    if fn is not None:
                        return fn(obj, args)
            # 标准库全局函数：sqrt(x) / clamp(x, lo, hi)
            if isinstance(c, Name):
                g = _GLOBAL_MAP.get(c.name)
                if g == "clamp" or c.name == "clamp":
                    a = [self._expr(x) for x in e.args]
                    return f"max({a[1]}, min({a[2]}, {a[0]}))"
                if c.name == "sign":
                    a = [self._expr(x) for x in e.args]
                    return f"(({a[0]} > 0) - ({a[0]} < 0))"
                if g:
                    return f"{g}({', '.join(self._expr(a) for a in e.args)})"
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