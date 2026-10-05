# -*- coding: utf-8 -*-
"""BlackLang → LLVM 后端（性能第三阶 · 真 LLVM）。

两条路径，共用同一份 **LLVM IR 文本**（零 Python 依赖即可生成）：

1. **AOT（默认）**：`compile_to_llvm_ir()` 生成 `.ll` → `clang -O2` 编译为本机码。
   clang 的后端就是 LLVM，所以这是货真价实的 LLVM 编译路径。
2. **真 JIT（可选）**：若 `llvmlite` 可用，`jit_run()` 用 MCJIT 在**进程内**把 IR
   直接编译成机器码并调用 —— 严格意义的 JIT。

指针模型自适应：LLVM ≥16 用 opaque pointer（`ptr`），LLVM ≤15 用 typed pointer
（`i8* / i64*`）。因此 AOT（clang 21）走 opaque，老 llvmlite 的 JIT 走 typed，两条路都通。

注意：macOS 上「加固运行时」的 Python 可能拒绝加载第三方 dylib（llvmlite 依赖），
此时 JIT 不可用，请改用 AOT（clang）路径；或换用普通 venv 的 python。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .ast_nodes import (
    Assign, Binary, Call, Compare, DictLit, ExprStmt, ForIn, FuncDef, GetAttr,
    GetItem, If, ListLit, Literal, Name, Program, Return, Sandbox, Unary, Use,
    While,
)
from .cbackend import CodeGenC, CBackendError, T_ANY, T_BOOL, T_FLOAT, T_INT, T_STRING

_LLVAL = {T_INT: "i64", T_FLOAT: "double", T_BOOL: "i64"}
_LLZERO = {T_INT: "0", T_FLOAT: "0.000000e+00", T_STRING: "null", T_BOOL: "0"}


def _ir_escape(s: str) -> str:
    """把 Python 字符串转成 LLVM c"..." 的转义体（按 UTF-8 字节）。"""
    out = []
    for b in s.encode("utf-8"):
        if b == 0x22:
            out.append("\\22")
        elif b == 0x5C:
            out.append("\\5C")
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(f"\\{b:02X}")
    return "".join(out)


def _fmt_operands(mode: str) -> Dict[str, str]:
    """格式字符串全局量「当作 i8* 用」时的操作数拼写。"""
    if mode == "opaque":
        return {k: f"@.{k}" for k in ("fmt_ll", "fmt_g", "dot0", "brk")}
    sizes = {"fmt_ll": 5, "fmt_g": 5, "dot0": 3, "brk": 6}
    return {
        k: f"getelementptr inbounds ([{n} x i8], [{n} x i8]* @.{k}, i64 0, i64 0)"
        for k, n in sizes.items()
    }


def _runtime_ir(P: str, PI: str, FMT: Dict[str, str]) -> str:
    """运行时辅助 IR。P=i8* 拼写, PI=i32* 拼写, FMT=格式量操作数。"""
    return f"""
@.fmt_ll = private constant [5 x i8] c"%lld\\00"
@.fmt_g  = private constant [5 x i8] c"%.*g\\00"
@.dot0  = private constant [3 x i8] c".0\\00"
@.brk   = private constant [6 x i8] c".eEni\\00"

declare i32 @printf({P}, ...)
declare {P} @malloc(i64)
declare i64 @strlen({P})
declare {P} @memcpy({P}, {P}, i64)
declare i32 @sprintf({P}, {P}, ...)
declare double @strtod({P}, {P})
declare {P} @strcat({P}, {P})
declare {P} @strpbrk({P}, {P})
declare i32 @strcmp({P}, {P})

define {P} @bl_concat({P} %a, {P} %b) {{
entry:
  %la = call i64 @strlen({P} %a)
  %lb = call i64 @strlen({P} %b)
  %n = add i64 %la, %lb
  %n1 = add i64 %n, 1
  %r = call {P} @malloc(i64 %n1)
  %x = call {P} @memcpy({P} %r, {P} %a, i64 %la)
  %p = getelementptr inbounds i8, {P} %r, i64 %la
  %lb1 = add i64 %lb, 1
  %y = call {P} @memcpy({P} %p, {P} %b, i64 %lb1)
  ret {P} %r
}}

define {P} @bl_str_ll(i64 %v) {{
entry:
  %b = call {P} @malloc(i64 32)
  %r = call i32 ({P}, {P}, ...) @sprintf({P} %b, {P} {FMT['fmt_ll']}, i64 %v)
  ret {P} %b
}}

define {P} @bl_str_d(double %v) {{
entry:
  %b = call {P} @malloc(i64 64)
  %prec = alloca i32
  store i32 15, {PI} %prec
  br label %loop
loop:
  %p = load i32, {PI} %prec
  %r = call i32 ({P}, {P}, ...) @sprintf({P} %b, {P} {FMT['fmt_g']}, i32 %p, double %v)
  %back = call double @strtod({P} %b, {P} null)
  %eq = fcmp oeq double %back, %v
  br i1 %eq, label %after, label %next
next:
  %p2 = add i32 %p, 1
  store i32 %p2, {PI} %prec
  %c = icmp sle i32 %p2, 17
  br i1 %c, label %loop, label %after
after:
  %hit = call {P} @strpbrk({P} %b, {P} {FMT['brk']})
  %isnull = icmp eq {P} %hit, null
  br i1 %isnull, label %adddot, label %done
adddot:
  %z = call {P} @strcat({P} %b, {P} {FMT['dot0']})
  br label %done
done:
  ret {P} %b
}}
"""


class CodeGenLLVM(CodeGenC):
    """复用 cbackend 的类型推断，改为发出 LLVM IR 文本。"""

    def __init__(self, program: Program, ptr_mode: str = "opaque"):
        super().__init__(program)
        self.ptr_mode = ptr_mode
        self.P = "ptr" if ptr_mode == "opaque" else "i8*"

    # ---- 类型拼写 ----
    def _ty(self, t: str) -> str:
        if t == T_STRING:
            return self.P
        return _LLVAL.get(t, "i64")

    def _pty(self, t: str) -> str:
        if self.ptr_mode == "opaque":
            return "ptr"
        if t == T_STRING:
            return "i8**"
        return _LLVAL.get(t, "i64") + "*"

    def _i32p(self) -> str:
        return "ptr" if self.ptr_mode == "opaque" else "i32*"

    # ---------------- 基础设施 ----------------
    def _reset_fn(self):
        self.blocks: List[List[Any]] = []
        self.cur = self._block("entry")
        self.allocas: List[str] = []
        self.tmp_n = 0
        self.term = False
        self.vars: Dict[str, str] = {}

    def _block(self, prefix: str) -> str:
        label = f"{prefix}{len(self.blocks)}"
        self.blocks.append([label, []])
        return label

    def _start(self, label: str):
        for b in self.blocks:
            if b[0] == label:
                self.cur = label
                self.term = False
                return
        raise CBackendError(f"内部错误：未定义的 IR 基本块 {label}")

    def _cur_lines(self) -> List[str]:
        for b in self.blocks:
            if b[0] == self.cur:
                return b[1]
        raise CBackendError("内部错误：当前基本块丢失")

    def emit(self, line: str):
        if self.term:
            self._start(self._block("dead"))
        self._cur_lines().append("  " + line)

    def end(self, line: str):
        if self.term:
            self._start(self._block("dead"))
        self._cur_lines().append("  " + line)
        self.term = True

    def tmp(self) -> str:
        self.tmp_n += 1
        return f"%t{self.tmp_n}"

    def _var(self, name: str, ty: str) -> str:
        if name not in self.vars:
            self.vars[name] = ty
            self.allocas.append(f"%{name} = alloca {self._ty(ty)}")
        return f"%{name}"

    def _new_scratch(self, ty: str) -> str:
        self.tmp_n += 1
        p = f"%sc{self.tmp_n}"
        self.allocas.append(f"{p} = alloca {self._ty(ty)}")
        return p

    def _intern_string(self, s: str) -> str:
        """注册字符串常量，返回可直接当 i8* 用的操作数。"""
        if s not in self.strings:
            name = f"@.s{len(self.strings)}"
            n = len(s.encode("utf-8")) + 1
            self.strings[s] = (name, n)
            self.string_globals.append(
                f'{name} = private constant [{n} x i8] c"{_ir_escape(s)}\\00"')
        name, n = self.strings[s]
        if self.ptr_mode == "opaque":
            return name
        t = self.tmp()
        self.emit(f"{t} = getelementptr inbounds [{n} x i8], [{n} x i8]* {name}, i64 0, i64 0")
        return t

    # ---------------- 类型辅助 ----------------
    def _lookup(self, name: str) -> str:
        if name in getattr(self, "vars", {}):
            return self.vars[name]
        cur = getattr(self, "_cur_locals", None)
        if cur and name in cur:
            return cur[name]
        if getattr(self, "var_types", None) and name in self.var_types:
            return self.var_types[name]
        return super()._lookup(name)

    def _truthy(self, val: str, ty: str) -> str:
        t = self.tmp()
        if ty == T_FLOAT:
            self.emit(f"{t} = fcmp une double {val}, 0.000000e+00")
        elif ty == T_STRING:
            self.emit(f"{t} = icmp ne {self.P} {val}, null")
        else:
            self.emit(f"{t} = icmp ne {self._ty(ty)} {val}, 0")
        return t

    def _to(self, val: str, src: str, dst: str) -> str:
        if src == dst or dst == T_ANY:
            return val
        if dst == T_FLOAT and src in (T_INT, T_BOOL):
            t = self.tmp()
            self.emit(f"{t} = sitofp {self._ty(src)} {val} to double")
            return t
        if dst in (T_INT, T_BOOL) and src == T_FLOAT:
            t = self.tmp()
            self.emit(f"{t} = fptosi double {val} to i64")
            return t
        if dst == T_STRING:
            return self._as_string(val, src)
        if dst in (T_INT, T_BOOL) and src == T_STRING:
            raise CBackendError("LLVM 后端：不支持字符串转数值")
        return val

    def _as_string(self, val: str, src: str) -> str:
        if src == T_STRING:
            return val
        t = self.tmp()
        if src == T_FLOAT:
            self.emit(f"{t} = call {self.P} @bl_str_d(double {val})")
        else:
            self.emit(f"{t} = call {self.P} @bl_str_ll(i64 {val})")
        return t

    # ---------------- 模块 ----------------
    def generate(self) -> str:
        self.strings: Dict[str, Tuple[str, int]] = {}
        self.string_globals: List[str] = []
        self._collect()
        self._infer_function_signatures()
        self._infer_variable_types()
        bodies = [self._emit_function(fd) for fd in self.funcs]
        bodies.append(self._emit_main())
        parts = [f"; BlackLang → LLVM IR（ptr mode: {self.ptr_mode}）", ""]
        if self.string_globals:
            parts.extend(self.string_globals)
            parts.append("")
        parts.append(_runtime_ir(self.P, self._i32p(), _fmt_operands(self.ptr_mode)).strip())
        parts.append("")
        parts.extend(bodies)
        return "\n".join(parts) + "\n"

    def _collect(self):
        try:
            super()._collect()
        except CBackendError as e:
            raise CBackendError(str(e).replace("C 后端", "LLVM 后端"))

    # ---------------- 函数 ----------------
    def _emit_function(self, fd: FuncDef) -> str:
        rt = self.return_types[fd.name]
        plist = []
        for i, p in enumerate(fd.params):
            ty = self.param_types[fd.name][i]
            plist.append(f"{self._ty(ty)} %arg_{p}")
        sig = ", ".join(plist)
        self._reset_fn()
        self._cur_fn_name = fd.name
        self._cur_locals = dict(getattr(fd, "_bl_locals", {}))
        for name, ty in self._cur_locals.items():
            self._var(name, ty)
        for i, p in enumerate(fd.params):
            ty = self.param_types[fd.name][i]
            self._var(p, ty)
            self.emit(f"store {self._ty(ty)} %arg_{p}, {self._pty(ty)} %{p}")
        for s in fd.body:
            self._stmt(s)
        if not self.term:
            self.end(f"ret {self._ty(rt)} {_LLZERO[rt]}")
        return self._render(f"define {self._ty(rt)} @bl_{fd.name}({sig})")

    def _emit_main(self) -> str:
        self._reset_fn()
        self._cur_fn_name = "<main>"
        self._cur_locals = dict(self.var_types)
        for name, ty in self._cur_locals.items():
            self._var(name, ty)
        for s in self.main_entries:
            self._stmt(s)
        if not self.term:
            self.end("ret i64 0")
        body = self._render("define i64 @bl_main()")
        wrapper = ["define i32 @main() {", "entry:",
                   "  %r = call i64 @bl_main()", "  ret i32 0", "}"]
        return body + "\n" + "\n".join(wrapper)

    def _render(self, header: str) -> str:
        lines = [header + " {"]
        entry_label, entry_lines = self.blocks[0]
        lines.append(entry_label + ":")
        for a in self.allocas:
            lines.append("  " + a)
        lines.extend(entry_lines)
        for label, blines in self.blocks[1:]:
            lines.append(label + ":")
            lines.extend(blines)
        lines.append("}")
        return "\n".join(lines)

    # ---------------- 语句 ----------------
    def _stmt(self, s):
        if isinstance(s, Assign):
            ty = self._lookup(s.name)
            if ty == T_ANY:
                ty = self._expr_type_safe(s.value)
            if ty == T_ANY:
                ty = T_INT
            ptr = self._var(s.name, ty)
            v = self._expr(s.value)
            v = self._to(v, self._expr_type(s.value), ty)
            self.emit(f"store {self._ty(ty)} {v}, {self._pty(ty)} {ptr}")
            return
        if isinstance(s, ExprStmt):
            self._expr(s.expr)
            return
        if isinstance(s, If):
            self._emit_if(s)
            return
        if isinstance(s, While):
            self._emit_while(s)
            return
        if isinstance(s, ForIn):
            self._emit_for(s)
            return
        if isinstance(s, Return):
            rt = self.return_types.get(self._cur_fn_name, T_INT)
            if s.value is None:
                self.end(f"ret {self._ty(rt)} {_LLZERO[rt]}")
            else:
                v = self._expr(s.value)
                v = self._to(v, self._expr_type(s.value), rt)
                self.end(f"ret {self._ty(rt)} {v}")
            return
        if isinstance(s, Sandbox):
            for b in s.body:
                self._stmt(b)
            return
        if isinstance(s, (FuncDef, Use)):
            return
        raise CBackendError(f"LLVM 后端：不支持的语句 {type(s).__name__}")

    def _emit_if(self, s: If):
        cond, body = s.branches[0]
        c = self._cond_i1(cond)
        then_l = self._block("then")
        after_l = self._block("after")
        has_else = len(s.branches) > 1 or bool(s.else_body)
        if has_else:
            else_l = self._block("else")
            self.end(f"br i1 {c}, label %{then_l}, label %{else_l}")
        else:
            else_l = None
            self.end(f"br i1 {c}, label %{then_l}, label %{after_l}")
        self._start(then_l)
        for b in body:
            self._stmt(b)
        if not self.term:
            self.end(f"br label %{after_l}")
        if else_l is not None:
            self._start(else_l)
            if len(s.branches) > 1:
                rest = If(branches=s.branches[1:], else_body=s.else_body)
            else:
                rest = If(branches=[(Literal(True), s.else_body or [])], else_body=None)
            self._emit_if(rest)
            if not self.term:
                self.end(f"br label %{after_l}")
        self._start(after_l)

    def _emit_while(self, s: While):
        cond_l = self._block("cond")
        body_l = self._block("body")
        after_l = self._block("after")
        self.end(f"br label %{cond_l}")
        self._start(cond_l)
        c = self._cond_i1(s.cond)
        self.end(f"br i1 {c}, label %{body_l}, label %{after_l}")
        self._start(body_l)
        for b in s.body:
            self._stmt(b)
        if not self.term:
            self.end(f"br label %{cond_l}")
        self._start(after_l)

    def _emit_for(self, s: ForIn):
        if not isinstance(s.iterable, ListLit):
            raise CBackendError("LLVM 后端：for-in 目前只支持列表字面量")
        for it in s.iterable.items:
            v = self._expr(it)
            ty = self._lookup(s.var)
            if ty == T_ANY:
                ty = self._expr_type(it)
            if ty == T_ANY:
                ty = T_INT
            ptr = self._var(s.var, ty)
            v = self._to(v, self._expr_type(it), ty)
            self.emit(f"store {self._ty(ty)} {v}, {self._pty(ty)} {ptr}")
            for b in s.body:
                self._stmt(b)

    # ---------------- 条件 ----------------
    def _cond_i1(self, e) -> str:
        if isinstance(e, Compare):
            return self._compare_i1(e)
        if isinstance(e, Binary) and e.op in ("and", "or"):
            a = self._cond_i1(e.left)
            pred = self.cur
            rhs_l = self._block("rhs")
            done_l = self._block("done")
            if e.op == "and":
                self.end(f"br i1 {a}, label %{rhs_l}, label %{done_l}")
            else:
                self.end(f"br i1 {a}, label %{done_l}, label %{rhs_l}")
            self._start(rhs_l)
            b = self._cond_i1(e.right)
            self.end(f"br label %{done_l}")
            self._start(done_l)
            r = self.tmp()
            short = "false" if e.op == "and" else "true"
            self.emit(f"{r} = phi i1 [ {short}, %{pred} ], [ {b}, %{rhs_l} ]")
            return r
        if isinstance(e, Unary) and e.op == "!":
            c = self._cond_i1(e.operand)
            t = self.tmp()
            self.emit(f"{t} = icmp eq i1 {c}, false")
            return t
        v = self._expr(e)
        return self._truthy(v, self._expr_type(e))

    def _compare_i1(self, e: Compare) -> str:
        lt, rt = self._expr_type(e.left), self._expr_type(e.right)
        if lt == T_STRING or rt == T_STRING:
            a = self._to(self._expr(e.left), lt, T_STRING)
            b = self._to(self._expr(e.right), rt, T_STRING)
            r = self.tmp()
            self.emit(f"{r} = call i32 @strcmp({self.P} {a}, {self.P} {b})")
            t = self.tmp()
            if e.op == "==":
                self.emit(f"{t} = icmp eq i32 {r}, 0")
            elif e.op == "!=":
                self.emit(f"{t} = icmp ne i32 {r}, 0")
            else:
                raise CBackendError(f"LLVM 后端：字符串不支持比较 '{e.op}'")
            return t
        t = _merge_ty(lt, rt)
        a = self._to(self._expr(e.left), lt, t)
        b = self._to(self._expr(e.right), rt, t)
        r = self.tmp()
        if t == T_FLOAT:
            op = {"==": "oeq", "!=": "one", "<": "olt", ">": "ogt",
                  "<=": "ole", ">=": "oge"}[e.op]
            self.emit(f"{r} = fcmp {op} double {a}, {b}")
        else:
            op = {"==": "eq", "!=": "ne", "<": "slt", ">": "sgt",
                  "<=": "sle", ">=": "sge"}[e.op]
            self.emit(f"{r} = icmp {op} i64 {a}, {b}")
        return r

    # ---------------- 表达式 ----------------
    def _expr(self, e) -> str:
        if isinstance(e, Literal):
            v = e.value
            if isinstance(v, bool):
                return "1" if v else "0"
            if isinstance(v, int):
                return str(v)
            if isinstance(v, float):
                return repr(v)
            if isinstance(v, str):
                return self._intern_string(v)
            raise CBackendError(f"LLVM 后端：不支持的字面量 {v!r}")
        if isinstance(e, Name):
            ty = self._lookup(e.name)
            if ty == T_ANY or e.name not in self.vars:
                raise CBackendError(f"LLVM 后端：未知变量 {e.name}")
            t = self.tmp()
            self.emit(f"{t} = load {self._ty(ty)}, {self._pty(ty)} %{e.name}")
            return t
        if isinstance(e, Unary):
            if e.op == "!":
                c = self._cond_i1(e.operand)
                t = self.tmp()
                self.emit(f"{t} = zext i1 {c} to i64")
                return t
            v = self._expr(e.operand)
            ty = self._expr_type(e.operand)
            t = self.tmp()
            if ty == T_FLOAT:
                self.emit(f"{t} = fneg double {v}")
            else:
                self.emit(f"{t} = sub i64 0, {v}")
            return t
        if isinstance(e, Compare):
            c = self._compare_i1(e)
            t = self.tmp()
            self.emit(f"{t} = zext i1 {c} to i64")
            return t
        if isinstance(e, Binary):
            return self._binary(e)
        if isinstance(e, Call):
            return self._call(e)
        raise CBackendError(f"LLVM 后端：不支持的表达式 {type(e).__name__}")

    def _binary(self, e: Binary) -> str:
        if e.op in ("and", "or"):
            lt, rt = self._expr_type(e.left), self._expr_type(e.right)
            t = _merge_ty(lt, rt)
            scratch = self._new_scratch(t)
            lv = self._to(self._expr(e.left), lt, t)
            self.emit(f"store {self._ty(t)} {lv}, {self._pty(t)} {scratch}")
            c = self._truthy(lv, t)
            rhs_l = self._block("rhs")
            done_l = self._block("done")
            if e.op == "and":
                self.end(f"br i1 {c}, label %{rhs_l}, label %{done_l}")
            else:
                self.end(f"br i1 {c}, label %{done_l}, label %{rhs_l}")
            self._start(rhs_l)
            rv = self._to(self._expr(e.right), rt, t)
            self.emit(f"store {self._ty(t)} {rv}, {self._pty(t)} {scratch}")
            self.end(f"br label %{done_l}")
            self._start(done_l)
            r = self.tmp()
            self.emit(f"{r} = load {self._ty(t)}, {self._pty(t)} {scratch}")
            return r
        lt, rt = self._expr_type(e.left), self._expr_type(e.right)
        if e.op == "+" and (lt == T_STRING or rt == T_STRING):
            a = self._as_string(self._expr(e.left), lt)
            b = self._as_string(self._expr(e.right), rt)
            t = self.tmp()
            self.emit(f"{t} = call {self.P} @bl_concat({self.P} {a}, {self.P} {b})")
            return t
        if lt == T_STRING or rt == T_STRING:
            raise CBackendError(f"LLVM 后端：字符串不支持运算 '{e.op}'")
        if e.op == "/":
            a = self._to(self._expr(e.left), lt, T_FLOAT)
            b = self._to(self._expr(e.right), rt, T_FLOAT)
            t = self.tmp()
            self.emit(f"{t} = fdiv double {a}, {b}")
            return t
        t = _merge_ty(lt, rt)
        a = self._to(self._expr(e.left), lt, t)
        b = self._to(self._expr(e.right), rt, t)
        r = self.tmp()
        if t == T_FLOAT:
            op = {"+": "fadd", "-": "fsub", "*": "fmul", "%": "frem"}[e.op]
            self.emit(f"{r} = {op} double {a}, {b}")
        else:
            op = {"+": "add", "-": "sub", "*": "mul", "%": "srem"}[e.op]
            self.emit(f"{r} = {op} i64 {a}, {b}")
        return r

    def _call(self, e: Call) -> str:
        if not isinstance(e.callee, Name):
            raise CBackendError("LLVM 后端：不支持动态调用")
        n = e.callee.name
        if n == "print":
            return self._emit_print(e.args)
        if n == "str":
            if not e.args:
                raise CBackendError("LLVM 后端：str() 需要参数")
            return self._as_string(self._expr(e.args[0]), self._expr_type(e.args[0]))
        if n == "int":
            return self._to(self._expr(e.args[0]), self._expr_type(e.args[0]), T_INT)
        if n == "float":
            return self._to(self._expr(e.args[0]), self._expr_type(e.args[0]), T_FLOAT)
        if n == "len":
            v = self._expr(e.args[0])
            t = self.tmp()
            self.emit(f"{t} = call i64 @strlen({self.P} {v})")
            return t
        if n in self.return_types:
            rt = self.return_types[n]
            args = []
            for i, a in enumerate(e.args):
                pt = self.param_types[n][i] if i < len(self.param_types[n]) else T_INT
                args.append(self._to(self._expr(a), self._expr_type(a), pt))
            t = self.tmp()
            # 现代 LLVM 文本 IR 要求实参带类型：call i64 @f(i64 %x)
            typed = ", ".join(
                f"{self._ty(self.param_types[n][i] if i < len(self.param_types[n]) else T_INT)} {a}"
                for i, a in enumerate(args))
            self.emit(f"{t} = call {self._ty(rt)} @bl_{n}({typed})")
            return t
        raise CBackendError(f"LLVM 后端：未知函数 {n}()")

    def _emit_print(self, args) -> str:
        parts: List[str] = []
        operands: List[str] = []
        for a in args:
            ty = self._expr_type(a)
            v = self._expr(a)
            if ty == T_STRING:
                parts.append("%s")
                operands.append(f"{self.P} {v}")
            elif ty == T_FLOAT:
                s = self._as_string(v, T_FLOAT)
                parts.append("%s")
                operands.append(f"{self.P} {s}")
            else:
                parts.append("%lld")
                operands.append(f"i64 {v}")
        fmt = " ".join(parts) + "\n"
        fname = self._intern_string(fmt)
        t = self.tmp()
        if operands:
            self.emit(f"{t} = call i32 ({self.P}, ...) @printf({self.P} {fname}, {', '.join(operands)})")
        else:
            self.emit(f"{t} = call i32 ({self.P}, ...) @printf({self.P} {fname})")
        return t


def _merge_ty(a: str, b: str) -> str:
    from .cbackend import _merge
    return _merge(a, b)


# ---------------- 对外接口 ----------------
def llvm_available() -> Optional[str]:
    from .cbackend import c_available
    return c_available()


def llvm_jit_version() -> Optional[int]:
    """返回 llvmlite 绑定的 LLVM 主版本号；不可用返回 None。"""
    try:
        from llvmlite import binding as b
        b.initialize()
        return int(b.llvm_version_info[0])
    except Exception:
        return None


def jit_available() -> bool:
    return llvm_jit_version() is not None


def jit_ptr_mode() -> str:
    v = llvm_jit_version()
    if v is None:
        return "opaque"
    return "opaque" if v >= 16 else "typed"


def compile_to_llvm_ir(program: Program, ptr_mode: str = "opaque") -> str:
    return CodeGenLLVM(program, ptr_mode=ptr_mode).generate()


_LAYOUT_CACHE: Dict[str, Tuple[str, str]] = {}


def host_target_info(cc: Optional[str] = None) -> Tuple[str, str]:
    """取本机 target datalayout / triple。

    varargs（printf/sprintf 的混合 int+double 实参）必须有正确的 datalayout，
    否则 ABI 降级会出错。这里向 clang 问一次并缓存。
    """
    key = cc or "auto"
    if key in _LAYOUT_CACHE:
        return _LAYOUT_CACHE[key]
    dl = tr = ""
    from .cbackend import c_available
    cc2 = cc or c_available()
    if cc2:
        try:
            p = subprocess.run(
                [cc2, "-S", "-emit-llvm", "-O0", "-x", "c", "-", "-o", "-"],
                input="int main(){return 0;}", capture_output=True, text=True, timeout=30)
            for line in p.stdout.splitlines():
                if line.startswith("target datalayout"):
                    dl = line.strip()
                elif line.startswith("target triple"):
                    tr = line.strip()
        except Exception:
            pass
    _LAYOUT_CACHE[key] = (dl, tr)
    return dl, tr


def _with_target(ir_text: str, datalayout: str, triple: str) -> str:
    """把 target datalayout/triple 注入 IR 头部（若尚未存在）。"""
    if (not datalayout and not triple) or "target datalayout" in ir_text or "target triple" in ir_text:
        return ir_text
    header = [x for x in (datalayout, triple) if x]
    return "\n".join(header) + "\n" + ir_text


def build_and_run_ir(ir_text: str, cc: Optional[str] = None, timeout: float = 60.0) -> Dict[str, Any]:
    """用 clang 把 LLVM IR 编译为本机码并运行。"""
    from .cbackend import c_available
    cc = cc or c_available()
    if cc is None:
        return {"ok": False, "stdout": "", "errors": ["未找到 C 编译器(clang)"]}
    dl, tr = host_target_info(cc)
    ir_text = _with_target(ir_text, dl, tr)
    workdir = tempfile.mkdtemp(prefix="bl_llvm_")
    ll = os.path.join(workdir, "program.ll")
    exe = os.path.join(workdir, "program")
    with open(ll, "w", encoding="utf-8") as f:
        f.write(ir_text)
    proc = subprocess.run([cc, "-O2", "-o", exe, ll, "-lm"],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        return {"ok": False, "stdout": "", "errors": ["LLVM IR 编译失败"],
                "cc_out": proc.stderr}
    run = subprocess.run([exe], capture_output=True, text=True, timeout=timeout)
    shutil.rmtree(workdir, ignore_errors=True)
    return {"ok": run.returncode == 0, "stdout": run.stdout,
            "errors": [] if run.returncode == 0 else [run.stderr.strip()],
            "cc_out": proc.stderr}


def jit_run(ir_text: str) -> Dict[str, Any]:
    """用 llvmlite MCJIT 在进程内编译并执行 bl_main()（捕获 printf 输出）。"""
    try:
        import ctypes
        import os as _os
        import tempfile as _tf
        from llvmlite import binding as llvm
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stdout": "", "errors": [f"llvmlite 不可用: {e}"], "jit": False}
    try:
        llvm.initialize()
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        target = llvm.Target.from_default_triple()
        tm = target.create_target_machine()
        # JIT 也要正确的 datalayout/triple（varargs ABI）
        try:
            dl = str(tm.target_data)
        except Exception:
            dl = ""
        header = []
        if dl and dl != "e":
            header.append(f'target datalayout = "{dl}"')
        if target.triple:
            header.append(f'target triple = "{target.triple}"')
        ir_text = _with_target(ir_text, "\n".join(header[:1]), header[1] if len(header) > 1 else "")
        mod = llvm.parse_assembly(ir_text)
        mod.verify()
        engine = llvm.create_mcjit_compiler(mod, tm)
        engine.finalize_object()
        engine.run_static_constructors()
        addr = engine.get_function_address("bl_main")
        if not addr:
            return {"ok": False, "stdout": "", "errors": ["未找到 bl_main"], "jit": True}
        fd, path = _tf.mkstemp(prefix="bl_jit_out_")
        saved = _os.dup(1)
        try:
            _os.dup2(fd, 1)
            fn = ctypes.CFUNCTYPE(ctypes.c_int64)(addr)
            fn()
            # JIT 代码走 libc stdio，先把缓冲刷进重定向的文件
            try:
                ctypes.CDLL(None).fflush(None)
            except Exception:
                pass
        finally:
            _os.dup2(saved, 1)
            _os.close(saved)
            _os.close(fd)
            with open(path, encoding="utf-8", errors="replace") as f:
                out = f.read()
            _os.unlink(path)
        return {"ok": True, "stdout": out, "errors": [], "jit": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stdout": "",
                "errors": [f"JIT 失败: {type(e).__name__}: {e}"], "jit": True}


def run_llvm(program: Program, use_jit: bool = False) -> Dict[str, Any]:
    """AST → LLVM IR → (JIT | clang 编译) → 运行。"""
    if use_jit and jit_available():
        mode = jit_ptr_mode()
        ir = compile_to_llvm_ir(program, ptr_mode=mode)
        res = jit_run(ir)
        res["ir"] = ir
        res["ptr_mode"] = mode
        res.setdefault("jit", True)
        return res
    ir = compile_to_llvm_ir(program, ptr_mode="opaque")
    res = build_and_run_ir(ir)
    res["ir"] = ir
    res["jit"] = False
    res["ptr_mode"] = "opaque"
    return res
