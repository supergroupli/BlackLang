# -*- coding: utf-8 -*-
"""BlackLang → C 代码生成后端（AOT→C 真编译，性能第三阶）。

把 BlackLang AST 编译成 C 源码，再用系统 C 编译器(clang/cc)编译成**原生可执行
文件**并运行。这是「介于 Python 与 C 之间」的最硬证据：算法核心以原生机器码执行。

支持子集（数值/字符串核心）：
  int / float / string / bool 字面量与变量、算术 + - * / %、比较 == != < > <= >=、
  逻辑 and/or、if/elif/else、while、for-in(列表字面量)、函数定义与递归、return、print、
  字符串拼接(含数值自动转字符串)。
不支持：interop(use …)、字典、列表变量、sandbox 运行期语义 —— 会抛出 `CBackendError`
并建议改用 VM / AOT→Python 路径（CLI 会自动回退）。

用法：
  from blacklang.cbackend import compile_to_c, build_and_run, c_available
  c_src = compile_to_c(program)
  res   = build_and_run(c_src)          # {"ok", "stdout", "errors", "exe"}
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .ast_nodes import (
    Assign, AttrAssign, Binary, Call, Compare, DictLit, ExprStmt, ForIn, FuncDef,
    GetAttr, GetItem, If, ListLit, Literal, Name, Program, Return, Sandbox, StructDef,
    Unary, Use, While,
)


class CBackendError(Exception):
    """C 后端无法编译该构造（应回退到 VM / AOT→Python 路径）。"""


# 类型名
T_INT, T_FLOAT, T_STRING, T_BOOL, T_ANY = "int", "float", "string", "bool", "any"

_C_TYPE = {T_INT: "long long", T_FLOAT: "double", T_STRING: "char *", T_BOOL: "long long"}
_ZERO = {T_INT: "0LL", T_FLOAT: "0.0", T_STRING: '""', T_BOOL: "0LL"}


def c_available() -> Optional[str]:
    """返回可用的 C 编译器路径，找不到返回 None。"""
    for cand in (os.environ.get("CC"), "cc", "clang", "gcc"):
        if not cand:
            continue
        path = shutil.which(cand)
        if path:
            return path
    return None


def _merge(a: str, b: str) -> str:
    if a == T_ANY:
        return b
    if b == T_ANY:
        return a
    if a == b:
        return a
    if a == T_STRING or b == T_STRING:
        # 允许 string + 数值（拼接）
        return T_STRING
    if a in (T_INT, T_FLOAT, T_BOOL) and b in (T_INT, T_FLOAT, T_BOOL):
        if a == T_FLOAT or b == T_FLOAT:
            return T_FLOAT
        return T_INT
    raise CBackendError(f"C 后端：类型不一致 {a} 与 {b}")


class CodeGenC:
    """把 BlackLang AST 编译为 C 源码。"""

    def __init__(self, program: Program):
        self.program = program
        self.funcs: List[FuncDef] = []
        self.uses: List[Use] = []
        self.main_entries: List[Any] = []
        self.var_types: Dict[str, str] = {}        # 全局/函数级变量 -> 类型
        self.param_types: Dict[str, List[str]] = {}  # 函数名 -> 参数类型
        self.return_types: Dict[str, str] = {}     # 函数名 -> 返回类型
        self._tmp = 0

    # ---------------- 入口 ----------------
    def generate(self) -> str:
        self._collect()
        self._infer_function_signatures()
        self._infer_variable_types()
        out: List[str] = []
        out.extend(self._header())
        # 函数原型
        for fd in self.funcs:
            out.append(self._proto(fd) + ";")
        out.append("")
        # 函数实现
        for fd in self.funcs:
            out.extend(self._emit_function(fd))
            out.append("")
        # main
        out.extend(self._emit_main())
        return "\n".join(out) + "\n"

    def _collect(self):
        for e in self.program.entries:
            if isinstance(e, FuncDef):
                self.funcs.append(e)
            elif isinstance(e, Use):
                self.uses.append(e)
            elif isinstance(e, Sandbox):
                if e.caps:
                    raise CBackendError(
                        "C 后端暂不支持 sandbox 块（权限为运行期语义）；请用 VM / AOT→Python 路径")
                self.main_entries.extend(e.body)
            else:
                self.main_entries.append(e)
        if self.uses:
            names = ", ".join(f"{u.lang}:{'/'.join(u.names)}" for u in self.uses)
            raise CBackendError(
                f"C 后端暂不支持互操作 use({names})；请用 VM / AOT→Python 路径")
        if any(isinstance(e, StructDef) for e in self.program.entries):
            raise CBackendError(
                "C 后端暂不支持 struct；请用 VM / AOT→Python 路径运行")
        if any(getattr(f, "name", "").count(".") for f in self.funcs):
            raise CBackendError(
                "C 后端暂不支持 struct 方法（Type.method）；请用 VM / AOT→Python 路径运行")

    def _header(self) -> List[str]:
        return [
            "/* 由 BlackLang AOT→C 后端生成 */",
            "#include <stdio.h>",
            "#include <stdlib.h>",
            "#include <string.h>",
            "#include <math.h>",
            "",
            "static char *bl_str_ll(long long v) {",
            '    char *b = (char *)malloc(32); sprintf(b, "%lld", v); return b;',
            "}",
            "static char *bl_str_d(double v) {",
            "    /* 最短可回读表示，尽量与 Python repr 一致 */",
            '    char *b = (char *)malloc(64);',
            "    for (int prec = 15; prec <= 17; prec++) {",
            '        sprintf(b, "%.*g", prec, v);',
            "        if (strtod(b, NULL) == v) break;",
            "    }",
            '    if (!strpbrk(b, ".eEni")) { strcat(b, ".0"); }',
            "    return b;",
            "}",
            "static char *bl_concat(const char *a, const char *b) {",
            "    size_t la = strlen(a), lb = strlen(b);",
            "    char *r = (char *)malloc(la + lb + 1);",
            "    memcpy(r, a, la); memcpy(r + la, b, lb + 1); return r;",
            "}",
            "",
        ]

    # ---------------- 类型推断 ----------------
    def _infer_function_signatures(self):
        # 参数：注解优先；否则从调用点实参类型合并；默认数值(int)
        ann = {fd.name: fd for fd in self.funcs}
        for fd in self.funcs:
            types = []
            for i, p in enumerate(fd.params):
                a = fd.param_types[i] if i < len(fd.param_types) else None
                types.append(a if a else None)
            self.param_types[fd.name] = [t or "int" for t in types]
            self.return_types[fd.name] = fd.return_type or "int"
        # 返回类型：无注解时由 return 语句推断
        for fd in self.funcs:
            if fd.return_type:
                continue
            found: List[str] = []

            def scan(stmts):
                for s in stmts:
                    if isinstance(s, Return) and s.value is not None:
                        t = self._expr_type_safe(s.value)
                        if t != T_ANY:
                            found.append(t)
                    elif isinstance(s, If):
                        for _, b in s.branches:
                            scan(b)
                        scan(s.else_body or [])
                    elif isinstance(s, While):
                        scan(s.body)
                    elif isinstance(s, ForIn):
                        scan(s.body)
                    elif isinstance(s, Sandbox):
                        scan(s.body)

            scan(fd.body)
            t = T_ANY
            for x in found:
                t = _merge(t, x)
            self.return_types[fd.name] = t if t != T_ANY else T_INT
        # 调用点实参类型（多轮，处理未注解参数）
        for _ in range(3):
            changed = False
            for fd in self.funcs:
                for call in self._calls_to(fd.name):
                    for i, arg in enumerate(call.args):
                        if i >= len(fd.params):
                            break
                        a = fd.param_types[i] if i < len(fd.param_types) else None
                        if a:
                            continue  # 注解优先
                        at = self._expr_type_safe(arg)
                        if at and at != T_ANY:
                            self.param_types[fd.name][i] = _merge(
                                self.param_types[fd.name][i], at)
                            changed = True
            if not changed:
                break

    def _calls_to(self, name: str) -> List[Call]:
        found: List[Call] = []

        def walk(e):
            if e is None:
                return
            if isinstance(e, Call):
                if isinstance(e.callee, Name) and e.callee.name == name:
                    found.append(e)
                walk(e.callee)
                for a in e.args:
                    walk(a)
            elif isinstance(e, Binary):
                walk(e.left); walk(e.right)
            elif isinstance(e, Unary):
                walk(e.operand)
            elif isinstance(e, Compare):
                walk(e.left); walk(e.right)
            elif isinstance(e, ListLit):
                for i in e.items:
                    walk(i)
            elif isinstance(e, GetItem):
                walk(e.obj); walk(e.index)
            elif isinstance(e, GetAttr):
                walk(e.obj)
            elif isinstance(e, DictLit):
                for k, v in e.pairs:
                    walk(k); walk(v)

        def walk_stmts(stmts):
            for s in stmts:
                if isinstance(s, Assign):
                    walk(s.value)
                elif isinstance(s, ExprStmt):
                    walk(s.expr)
                elif isinstance(s, If):
                    for c, b in s.branches:
                        walk(c); walk_stmts(b)
                    walk_stmts(s.else_body or [])
                elif isinstance(s, While):
                    walk(s.cond); walk_stmts(s.body)
                elif isinstance(s, ForIn):
                    walk(s.iterable); walk_stmts(s.body)
                elif isinstance(s, Return):
                    walk(s.value)
                elif isinstance(s, Sandbox):
                    walk_stmts(s.body)

        for fd in self.funcs:
            walk_stmts(fd.body)
        walk_stmts(self.main_entries)
        return found

    def _infer_variable_types(self):
        # 全局 + 每个函数：合并所有赋值的类型
        def scan(stmts, table):
            for s in stmts:
                if isinstance(s, Assign):
                    t = self._expr_type_safe(s.value)
                    if s.type_annotation:
                        t = s.type_annotation
                    if t and t != T_ANY:
                        table[s.name] = _merge(table.get(s.name, T_ANY), t)
                    else:
                        table.setdefault(s.name, T_INT)
                elif isinstance(s, If):
                    for _, b in s.branches:
                        scan(b, table)
                    scan(s.else_body or [], table)
                elif isinstance(s, While):
                    scan(s.body, table)
                elif isinstance(s, ForIn):
                    scan(s.body, table)
                elif isinstance(s, Sandbox):
                    scan(s.body, table)

        for fd in self.funcs:
            t: Dict[str, str] = {}
            for i, p in enumerate(fd.params):
                t[p] = self.param_types[fd.name][i]
            scan(fd.body, t)
            fd._bl_locals = t  # type: ignore[attr-defined]
        g: Dict[str, str] = {}
        scan(self.main_entries, g)
        self.var_types = g

    def _expr_type_safe(self, e) -> str:
        try:
            return self._expr_type(e)
        except CBackendError:
            return T_ANY

    def _expr_type(self, e) -> str:
        if isinstance(e, Literal):
            v = e.value
            if isinstance(v, bool):
                return T_BOOL
            if isinstance(v, int):
                return T_INT
            if isinstance(v, float):
                return T_FLOAT
            if isinstance(v, str):
                return T_STRING
            return T_ANY
        if isinstance(e, Name):
            return self._lookup(e.name)
        if isinstance(e, Unary):
            t = self._expr_type(e.operand)
            return T_BOOL if e.op == "!" else t
        if isinstance(e, Compare):
            return T_BOOL
        if isinstance(e, Binary):
            if e.op in ("and", "or"):
                return _merge(self._expr_type(e.left), self._expr_type(e.right))
            lt, rt = self._expr_type(e.left), self._expr_type(e.right)
            if e.op == "+":
                if lt == T_STRING or rt == T_STRING:
                    return T_STRING
            # BL 的 '/' 是真除法：结果恒为 float
            if e.op == "/":
                if lt == T_STRING or rt == T_STRING:
                    raise CBackendError("C 后端：字符串不支持 '/'")
                return T_FLOAT
            return _merge(lt, rt)
        if isinstance(e, Call):
            if isinstance(e.callee, Name):
                n = e.callee.name
                if n in self.return_types:
                    return self.return_types[n]
                if n in ("str",):
                    return T_STRING
                if n in ("int",):
                    return T_INT
                if n in ("float",):
                    return T_FLOAT
                if n == "len":
                    return T_INT
                raise CBackendError(f"C 后端：未知函数 {n}")
            raise CBackendError("C 后端：不支持动态调用")
        raise CBackendError(f"C 后端：不支持的表达式 {type(e).__name__}")

    def _lookup(self, name: str) -> str:
        for fd in self.funcs:
            t = getattr(fd, "_bl_locals", {})
            if name in t:
                return t[name]
        return self.var_types.get(name, T_ANY)

    # ---------------- 函数 ----------------
    def _proto(self, fd: FuncDef) -> str:
        rt = _C_TYPE[self.return_types[fd.name]]
        params = ", ".join(
            f"{_C_TYPE[self.param_types[fd.name][i]]} {p}"
            for i, p in enumerate(fd.params))
        return f"static {rt} bl_{fd.name}({params if params else 'void'})"

    def _emit_function(self, fd: FuncDef) -> List[str]:
        out = [self._proto(fd) + " {"]
        self._locals = dict(getattr(fd, "_bl_locals", {}))
        self._declared = set(fd.params)
        self._params = set(fd.params)
        body = self._stmts(fd.body)
        out.extend(self._locals_decls())
        out.extend("    " + ln for ln in body)
        rt = self.return_types[fd.name]
        if rt == T_STRING:
            out.append('    return "";')
        elif rt == T_FLOAT:
            out.append("    return 0.0;")
        else:
            out.append("    return 0;")
        out.append("}")
        return out

    def _emit_main(self) -> List[str]:
        self._locals = dict(self.var_types)
        self._declared = set()
        self._params = set()
        body = self._stmts(self.main_entries)
        out = ["int main(void) {"]
        out.extend(self._locals_decls())
        out.extend("    " + ln for ln in body)
        out.append("    return 0;")
        out.append("}")
        return out

    def _locals_decls(self) -> List[str]:
        lines = []
        for name in sorted(self._declared):
            if name in self._params:
                continue
            t = self._locals.get(name, T_INT)
            if t == T_ANY:
                t = T_INT
            lines.append(f"    {_C_TYPE[t]} {name} = {_ZERO[t]};")
        return lines

    # ---------------- 语句 ----------------
    def _stmts(self, stmts) -> List[str]:
        out: List[str] = []
        for s in stmts:
            out.extend(self._stmt(s))
        return out

    def _stmt(self, s) -> List[str]:
        if isinstance(s, Assign):
            self._declared.add(s.name)
            self._locals.setdefault(s.name, self._expr_type_safe(s.value))
            return [f"{s.name} = {self._expr(s.value)};"]
        if isinstance(s, ExprStmt):
            if isinstance(s.expr, Call):
                return [self._expr(s.expr) + ";"]
            return [f"(void)({self._expr(s.expr)});"]
        if isinstance(s, If):
            out: List[str] = []
            first = True
            for cond, body in s.branches:
                kw = "if" if first else "else if"
                out.append(f"{kw} ({self._cond(cond)}) {{")
                out.extend("    " + ln for ln in self._stmts(body))
                out.append("}")
                first = False
            if s.else_body:
                out.append("else {")
                out.extend("    " + ln for ln in self._stmts(s.else_body))
                out.append("}")
            return out
        if isinstance(s, While):
            out = [f"while ({self._cond(s.cond)}) {{"]
            out.extend("    " + ln for ln in self._stmts(s.body))
            out.append("}")
            return out
        if isinstance(s, ForIn):
            # 仅支持列表字面量：展开为 C 数组 + 索引循环
            if not isinstance(s.iterable, ListLit):
                raise CBackendError("C 后端：for-in 目前只支持列表字面量，请用 VM / AOT→Python")
            items = s.iterable.items
            et = T_ANY
            for it in items:
                et = _merge(et, self._expr_type(it))
            if et == T_ANY:
                et = T_INT
            self._tmp += 1
            arr = f"_bl_arr{self._tmp}"
            idx = f"_bl_i{self._tmp}"
            self._declared.add(s.var)
            self._locals.setdefault(s.var, et)
            vals = ", ".join(self._coerce_to(it, et) for it in items)
            out = [
                f"{_C_TYPE[et]} {arr}[] = {{{vals if vals else '0'}}};",
                f"for (size_t {idx} = 0; {idx} < {len(items)}; ++{idx}) {{",
                f"    {s.var} = {arr}[{idx}];",
            ]
            self._declared.discard(s.var)
            out.extend("    " + ln for ln in self._stmts(s.body))
            out.append("}")
            self._declared.add(s.var)
            return out
        if isinstance(s, Return):
            if s.value is None:
                return ["return 0;"]
            return [f"return {self._expr(s.value)};"]
        if isinstance(s, Sandbox):
            return self._stmts(s.body)
        if isinstance(s, (FuncDef, Use)):
            return []
        raise CBackendError(f"C 后端：不支持的语句 {type(s).__name__}")

    # ---------------- 表达式 ----------------
    def _cond(self, e) -> str:
        """布尔上下文（if/while 条件）。"""
        if isinstance(e, Compare):
            return self._compare(e)
        if isinstance(e, Binary) and e.op in ("and", "or"):
            op = "&&" if e.op == "and" else "||"
            return f"({self._cond(e.left)} {op} {self._cond(e.right)})"
        if isinstance(e, Unary) and e.op == "!":
            return f"(!({self._cond(e.operand)}))"
        t = self._expr_type(e)
        if t == T_STRING:
            return f"(({self._expr(e)})[0] != 0)"
        return f"({self._expr(e)} != 0)"

    def _compare(self, e: Compare) -> str:
        lt, rt = self._expr_type(e.left), self._expr_type(e.right)
        if lt == T_STRING or rt == T_STRING:
            a, b = self._expr(e.left), self._expr(e.right)
            if e.op == "==":
                return f"(strcmp({a}, {b}) == 0)"
            if e.op == "!=":
                return f"(strcmp({a}, {b}) != 0)"
            raise CBackendError(f"C 后端：字符串不支持比较 '{e.op}'")
        t = _merge(lt, rt)
        a = self._coerce_to(e.left, t)
        b = self._coerce_to(e.right, t)
        return f"({a} {e.op} {b})"

    def _expr(self, e) -> str:
        if isinstance(e, Literal):
            v = e.value
            if isinstance(v, bool):
                return "1LL" if v else "0LL"
            if isinstance(v, int):
                return f"{v}LL"
            if isinstance(v, float):
                return repr(v)
            if isinstance(v, str):
                return self._c_string(v)
            raise CBackendError(f"C 后端：不支持的字面量 {v!r}")
        if isinstance(e, Name):
            if e.name in ("true", "false"):
                return "1LL" if e.name == "true" else "0LL"
            return e.name
        if isinstance(e, Unary):
            if e.op == "!":
                return f"(!({self._cond(e.operand)}))"
            return f"(-({self._expr(e.operand)}))"
        if isinstance(e, Compare):
            return self._compare(e)
        if isinstance(e, Binary):
            return self._binary(e)
        if isinstance(e, Call):
            return self._call(e)
        if isinstance(e, ListLit):
            raise CBackendError("C 后端：列表仅可用于 for-in 字面量")
        raise CBackendError(f"C 后端：不支持的表达式 {type(e).__name__}")

    def _binary(self, e: Binary) -> str:
        if e.op in ("and", "or"):
            # 与 VM 一致的「返回值」语义：and → 左假取左，否则取右；or → 左真取左，否则取右
            lt = self._expr_type(e.left)
            rt = self._expr_type(e.right)
            t = _merge(lt, rt)
            a = self._coerce_to(e.left, t)
            b = self._coerce_to(e.right, t)
            if t == T_STRING:
                if e.op == "and":
                    return f"(({a})[0] ? ({b}) : ({a}))"
                return f"(({a})[0] ? ({a}) : ({b}))"
            if e.op == "and":
                return f"(({a}) ? ({b}) : ({a}))"
            return f"(({a}) ? ({a}) : ({b}))"
        lt, rt = self._expr_type(e.left), self._expr_type(e.right)
        # 字符串拼接
        if e.op == "+" and (lt == T_STRING or rt == T_STRING):
            return f"bl_concat({self._as_string(e.left)}, {self._as_string(e.right)})"
        if lt == T_STRING or rt == T_STRING:
            raise CBackendError(f"C 后端：字符串不支持运算 '{e.op}'")
        t = _merge(lt, rt)
        a = self._coerce_to(e.left, t)
        b = self._coerce_to(e.right, t)
        op = e.op
        if op == "/":
            return f"((double)({a}) / (double)({b}))"
        if op == "%" and t == T_FLOAT:
            return f"fmod(({a}), ({b}))"
        return f"({a} {op} {b})"

    def _call(self, e: Call) -> str:
        if isinstance(e.callee, Name):
            n = e.callee.name
            if n == "print":
                if not e.args:
                    return 'printf("\\n")'
                parts = []
                for a in e.args:
                    t = self._expr_type(a)
                    v = self._expr(a)
                    if t == T_STRING:
                        parts.append(f'printf("%s", {v})')
                    elif t == T_FLOAT:
                        parts.append(f'printf("%s", bl_str_d({v}))')
                    elif t == T_BOOL:
                        parts.append(f'printf("%lld", (long long)({v}))')
                    else:
                        parts.append(f'printf("%lld", (long long)({v}))')
                # 拼接 + 换行
                expr = parts[0]
                for p in parts[1:]:
                    expr = f'({expr}, printf(" "), {p})'
                return f"({expr}, printf(\"\\n\"))"
            if n in ("str",):
                if not e.args:
                    raise CBackendError("C 后端：str() 需要 1 个参数")
                return self._as_string(e.args[0])
            if n in ("int", "float", "len"):
                if not e.args:
                    raise CBackendError(f"C 后端：{n}() 需要参数")
                t = self._expr_type(e.args[0])
                if n == "len" and t == T_STRING:
                    return f"((long long)strlen({self._expr(e.args[0])}))"
                if n == "float":
                    return f"((double)({self._coerce_to(e.args[0], T_FLOAT)}))"
                return f"((long long)({self._coerce_to(e.args[0], T_INT)}))"
            if n in self.return_types:
                rt = self.return_types[n]
                args = []
                for i, a in enumerate(e.args):
                    pt = self.param_types[n][i] if i < len(self.param_types[n]) else T_INT
                    args.append(self._coerce_to(a, pt))
                return f"bl_{n}({', '.join(args) if args else ''})"
            raise CBackendError(f"C 后端：未知函数 {n}()")
        raise CBackendError("C 后端：不支持动态调用")

    # ---------------- 类型转换/辅助 ----------------
    def _coerce_to(self, e, t: str) -> str:
        v = self._expr(e)
        if t == T_ANY:
            return v
        src = self._expr_type(e)
        if src == t:
            return v
        if t == T_FLOAT and src in (T_INT, T_BOOL):
            return f"((double)({v}))"
        if t in (T_INT, T_BOOL) and src == T_FLOAT:
            return f"((long long)({v}))"
        if t == T_STRING:
            return self._as_string(e)
        return v

    def _as_string(self, e) -> str:
        t = self._expr_type(e)
        v = self._expr(e)
        if t == T_STRING:
            return v
        if t == T_FLOAT:
            return f"bl_str_d({v})"
        if t == T_BOOL:
            return f"bl_str_ll((long long)({v}))"
        return f"bl_str_ll((long long)({v}))"

    def _c_string(self, s: str) -> str:
        out = ['"']
        for ch in s:
            if ch == "\\":
                out.append("\\\\")
            elif ch == '"':
                out.append('\\"')
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\r":
                out.append("\\r")
            elif ord(ch) < 32:
                out.append(f"\\{ord(ch):03o}")
            else:
                out.append(ch)
        out.append('"')
        return "".join(out)


def compile_to_c(program: Program) -> str:
    return CodeGenC(program).generate()


def build_and_run(c_src: str, cc: Optional[str] = None, keep: bool = False,
                  timeout: float = 30.0) -> Dict[str, Any]:
    """编译 C 源码为原生可执行文件并运行，返回 {"ok","stdout","errors","exe"/"cc_out"}。"""
    cc = cc or c_available()
    if cc is None:
        return {"ok": False, "stdout": "", "errors": ["未找到 C 编译器(cc/clang/gcc)"],
                "cc_out": ""}
    workdir = tempfile.mkdtemp(prefix="bl_c_")
    c_path = os.path.join(workdir, "program.c")
    exe = os.path.join(workdir, "program")
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(c_src)
    proc = subprocess.run([cc, "-O2", "-std=c11", "-o", exe, c_path, "-lm"],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        return {"ok": False, "stdout": "", "errors": ["C 编译失败"], "cc_out": proc.stderr}
    run = subprocess.run([exe], capture_output=True, text=True, timeout=timeout)
    if not keep:
        shutil.rmtree(workdir, ignore_errors=True)
        exe_out = ""
    else:
        exe_out = exe
    return {"ok": run.returncode == 0, "stdout": run.stdout,
            "errors": [] if run.returncode == 0 else [run.stderr.strip()],
            "exe": exe_out, "cc_out": proc.stderr}


def run_native(program: Program) -> Dict[str, Any]:
    """一步到位：AST → C 源码 → 原生编译 → 运行。"""
    c_src = compile_to_c(program)
    res = build_and_run(c_src)
    res["c_source"] = c_src
    return res
