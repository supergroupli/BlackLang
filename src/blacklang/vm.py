# -*- coding: utf-8 -*-
"""BlackLang 字节码编译器 + 栈式虚拟机（性能路径）。

把 AST 编译为扁平字节码（op, arg）对，用显式操作数栈 + 局部变量表执行。
用户函数全部编译为 `Func`，执行前注册进 globals（funcref），调用与递归在 VM
内进行，无 Python 递归栈开销。相比树遍历解释器通常快数倍，是性能目标的第一级。
"""
from __future__ import annotations

import operator as _op
from typing import Any, Dict, List

from .ast_nodes import (
    Assign, AttrAssign, Binary, Call, Compare, DictLit, ExprStmt, ForIn, FuncDef,
    GetAttr, GetItem, If, ListLit, Literal, Name, Program, Return, Sandbox,
    StructDef, Unary, Use, While,
)
from .interop import InteropValue
from .values import BoundMethod, StructInstance

# ---- 操作码 ----
PUSH, LOAD, STORE, LLOAD, LSTORE, BINOP, UOP, CMP, JUMP, JIF, JIT, DUP, \
    POPOP, CALL, RET, MLIST, GETITEM, GETATTR, MAKEDICT, ATTRSET = range(20)

OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD = 0, 1, 2, 3, 4
OP_EQ, OP_NE, OP_LT, OP_GT, OP_LE, OP_GE = 5, 6, 7, 8, 9, 10
OP_NEG, OP_NOT = 11, 12
OP_LEN = 100

_BIN = {OP_ADD: _op.add, OP_SUB: _op.sub, OP_MUL: _op.mul,
        OP_DIV: _op.truediv, OP_MOD: _op.mod}
_CMP = {OP_EQ: _op.eq, OP_NE: _op.ne, OP_LT: _op.lt, OP_GT: _op.gt,
        OP_LE: _op.le, OP_GE: _op.ge}
_CMPOP = {"<": OP_LT, ">": OP_GT, "<=": OP_LE, ">=": OP_GE,
          "==": OP_EQ, "!=": OP_NE}
_BINOP = {"+": OP_ADD, "-": OP_SUB, "*": OP_MUL, "/": OP_DIV, "%": OP_MOD}


class Func:
    __slots__ = ("name", "code", "consts", "names", "nargs", "nlocals")

    def __init__(self, name, code, consts, names, nargs, nlocals):
        self.name = name
        self.code = code
        self.consts = consts
        self.names = names
        self.nargs = nargs
        self.nlocals = nlocals


class _FC:
    """每个函数的代码/常量/名字缓冲。"""
    def __init__(self):
        self.code: List[int] = []
        self.consts: List[Any] = []
        self.names: List[str] = []
        self._cmap: Dict[Any, int] = {}   # key=(type,value) -> 避免 1 与 True 混淆

    def e(self, op, arg=0):
        self.code.append(op)
        self.code.append(arg)

    def pc(self):
        return len(self.code)

    def patch(self, at, target):
        self.code[at + 1] = target


def _ic(fc, v):
    # 用 (type, value) 作键，避免 1 与 True（==1）intern 冲突
    key = (type(v), v)
    if key in fc._cmap:
        return fc._cmap[key]
    i = len(fc.consts)
    fc.consts.append(v)
    fc._cmap[key] = i
    return i


def _in(fc, n):
    try:
        return fc.names.index(n)
    except ValueError:
        fc.names.append(n)
        return len(fc.names) - 1


class Compiler:
    def __init__(self, program: Program):
        self.program = program
        self.funcs: Dict[str, Func] = {}

    def compile(self) -> Dict[str, Func]:
        self.structs: Dict[str, List[str]] = {
            e.name: [f[0] for f in e.fields]
            for e in self.program.entries if isinstance(e, StructDef)}
        fn_names = {e.name for e in self.program.entries if isinstance(e, FuncDef)}
        # 先编译用户函数（递归可见性通过 globals 注册解决）
        for e in self.program.entries:
            if isinstance(e, FuncDef):
                self.funcs[e.name] = self._compile_fn(e)
        main = self._compile_fc("<main>", [], self.program.entries, fn_names)
        self.funcs["<main>"] = main
        return self.funcs

    def _compile_fn(self, node: FuncDef) -> Func:
        fn_names = {n for n, f in self.funcs.items()}
        fc = self._compile_fc(node.name, node.params, node.body, fn_names)
        return Func(node.name, fc.code, fc.consts, fc.names, len(node.params), len(fc.code))

    def _compile_fc(self, name, params, body, fn_names) -> Func:
        self._slots = len(params)        # 单调槽位计数：局部变量 + 隐藏循环槽
        fc = _FC()
        scope = {p: i for i, p in enumerate(params)}
        # ★ 预登记函数体内所有 `let` 声明为真正的局部槽 —— 保证递归可重入
        for decl in _collect_let_names(body):
            if decl not in scope:
                scope[decl] = self._slots
                self._slots += 1
        for s in body:
            self._stmt(s, fc, scope, fn_names)
        fc.e(RET)
        return Func(name, fc.code, fc.consts, fc.names, len(params), self._slots)

    # ---- statements ----
    def _stmt(self, s, fc, scope, fn_names):
        if isinstance(s, Assign):
            self._expr(s.value, fc, scope, fn_names)
            if s.name not in scope and getattr(s, "is_decl", False):
                # 兜底：预扫描漏掉的 let（如动态构造的语句）就地分配局部槽
                scope[s.name] = self._slots
                self._slots += 1
            if s.name in scope:
                fc.e(LSTORE, scope[s.name])
            else:
                fc.e(STORE, _in(fc, s.name))
        elif isinstance(s, AttrAssign):
            self._expr(s.obj, fc, scope, fn_names)
            self._expr(s.value, fc, scope, fn_names)
            fc.e(ATTRSET, _in(fc, s.attr))
        elif isinstance(s, StructDef):
            pass  # struct 定义只用于登记，不产生代码
        elif isinstance(s, ExprStmt):
            self._expr(s.expr, fc, scope, fn_names)
            if not isinstance(s.expr, Call):
                fc.e(POP, 0)  # 丢弃多余值
        elif isinstance(s, If):
            ends = []
            for cond, body in s.branches:
                self._expr(cond, fc, scope, fn_names)
                jf = fc.pc()
                fc.e(JIF, 0)
                for b in body:
                    self._stmt(b, fc, scope, fn_names)
                j = fc.pc()
                fc.e(JUMP, 0)
                ends.append(j)
                fc.patch(jf, fc.pc())
            if s.else_body:
                for b in s.else_body:
                    self._stmt(b, fc, scope, fn_names)
            for j in ends:
                fc.patch(j, fc.pc())
        elif isinstance(s, While):
            start = fc.pc()
            self._expr(s.cond, fc, scope, fn_names)
            jf = fc.pc()
            fc.e(JIF, 0)
            for b in s.body:
                self._stmt(b, fc, scope, fn_names)
            fc.e(JUMP, start)
            fc.patch(jf, fc.pc())
        elif isinstance(s, ForIn):
            self._for(s, fc, scope, fn_names)
        elif isinstance(s, Return):
            if s.value:
                self._expr(s.value, fc, scope, fn_names)
            else:
                fc.e(PUSH, _ic(fc, None))
            fc.e(RET)
        elif isinstance(s, Sandbox):
            # 能力已在编译期由 checker 保证；VM 不重复强制
            for b in s.body:
                self._stmt(b, fc, scope, fn_names)
        elif isinstance(s, (Use, FuncDef)):
            pass

    def _for(self, s, fc, scope, fn_names):
        # 隐式槽：iter_list, index, var —— 用单调槽位计数避免嵌套复用冲突
        list_slot = self._slots; self._slots += 1
        idx_slot = self._slots; self._slots += 1
        var_slot = self._slots; self._slots += 1
        scope[s.var] = var_slot     # 先注册，便于 body 内引用
        self._expr(s.iterable, fc, scope, fn_names)   # push list
        fc.e(LSTORE, list_slot)
        fc.e(PUSH, _ic(fc, 0))
        fc.e(LSTORE, idx_slot)
        cond = fc.pc()
        # 条件：idx < len(list)
        fc.e(LLOAD, idx_slot)
        fc.e(LOAD, _in(fc, "len"))       # callee 在参数之下
        fc.e(LLOAD, list_slot)
        fc.e(CALL, 1)                    # push len(list)
        fc.e(CMP, OP_LT)
        jf = fc.pc()
        fc.e(JIF, 0)
        # 取值 var = list[idx]
        fc.e(LLOAD, list_slot)
        fc.e(LLOAD, idx_slot)
        fc.e(GETITEM)
        fc.e(LSTORE, var_slot)
        for b in s.body:
            self._stmt(b, fc, scope, fn_names)
        # idx += 1
        fc.e(LLOAD, idx_slot)
        fc.e(PUSH, _ic(fc, 1))
        fc.e(BINOP, OP_ADD)
        fc.e(LSTORE, idx_slot)
        fc.e(JUMP, cond)
        fc.patch(jf, fc.pc())
        # 从作用域移除 var（以免函数结束后残留），且 CMP 隐式槽保留仅供本循环使用
        del scope[s.var]

    # ---- expressions ----
    def _expr(self, e, fc, scope, fn_names):
        if isinstance(e, Literal):
            fc.e(PUSH, _ic(fc, e.value))
        elif isinstance(e, Name):
            if e.name in scope:
                fc.e(LLOAD, scope[e.name])
            else:
                fc.e(LOAD, _in(fc, e.name))
        elif isinstance(e, Binary):
            self._binary(e, fc, scope, fn_names)
        elif isinstance(e, Unary):
            self._expr(e.operand, fc, scope, fn_names)
            fc.e(UOP, OP_NEG if e.op == "-" else OP_NOT)
        elif isinstance(e, Compare):
            self._expr(e.left, fc, scope, fn_names)
            self._expr(e.right, fc, scope, fn_names)
            fc.e(CMP, _CMPOP[e.op])
        elif isinstance(e, Call):
            self._expr(e.callee, fc, scope, fn_names)
            for a in e.args:
                self._expr(a, fc, scope, fn_names)
            fc.e(CALL, len(e.args))
        elif isinstance(e, ListLit):
            for i in e.items:
                self._expr(i, fc, scope, fn_names)
            fc.e(MLIST, len(e.items))
        elif isinstance(e, DictLit):
            for k, v in e.pairs:
                self._expr(k, fc, scope, fn_names)
                self._expr(v, fc, scope, fn_names)
            fc.e(MAKEDICT, len(e.pairs))
        elif isinstance(e, GetItem):
            self._expr(e.obj, fc, scope, fn_names)
            self._expr(e.index, fc, scope, fn_names)
            fc.e(GETITEM)
        elif isinstance(e, GetAttr):
            self._expr(e.obj, fc, scope, fn_names)
            fc.e(GETATTR, _in(fc, e.attr))

    def _binary(self, e, fc, scope, fn_names):
        if e.op in ("and", "or"):
            # and: a; dup; JIF->L;  pop; b;  JUMP->L; L:   (a 假→a，否则 b)
            # or:  a; dup; JIT->L;  pop; b;  JUMP->L; L:   (a 真→a，否则 b)
            j_op = JIF if e.op == "and" else JIT
            self._expr(e.left, fc, scope, fn_names)
            fc.e(DUP)
            jz = fc.pc()
            fc.e(j_op, 0)
            fc.e(POPOP)
            self._expr(e.right, fc, scope, fn_names)
            j = fc.pc()
            fc.e(JUMP, 0)
            l_end = fc.pc()
            fc.patch(jz, l_end)
            fc.patch(j, l_end)
            return
        self._expr(e.left, fc, scope, fn_names)
        self._expr(e.right, fc, scope, fn_names)
        fc.e(BINOP, _BINOP[e.op])


class _UserFunc:
    __slots__ = ("func",)
    def __init__(self, func):
        self.func = func


class _StructCtor:
    """struct 构造器：Point(3, 4) → StructInstance。"""
    __slots__ = ("name", "fields")

    def __init__(self, name, fields):
        self.name = name
        self.fields = list(fields)


class VM:
    def __init__(self, funcs: Dict[str, Func], builtins: dict = None, interop=None,
                 uses: List = None, structs: Dict[str, List[str]] = None):
        self.structs = structs or {}
        self.funcs = funcs
        self.builtins = builtins or {}
        self.interop = interop
        self.globals: Dict[str, Any] = {}
        # 注册用户函数为 globals（funcref），保证递归/互调
        for name, f in funcs.items():
            if name != "<main>":
                self.globals[name] = _UserFunc(f)
        # ★ struct 构造器注册为可调用全局（Point(...) 直接走 LOAD Point + CALL）
        for sname, sfields in self.structs.items():
            self.globals[sname] = _StructCtor(sname, sfields)
        # 处理互操作绑定 use
        if uses and interop is not None:
            for u in uses:
                try:
                    interop.bind(u.lang, u.names)
                except Exception:
                    pass
                for n in u.names:
                    proxy = interop.resolve_python(n) if u.lang == "python" else None
                    if proxy is None and u.lang == "node":
                        proxy = interop.resolve_node(n)
                    if proxy is not None:
                        self.globals[n] = proxy

    def run(self, fname="<main>", args=None):
        f = self.funcs[fname]
        stack: List[Any] = []
        return self._exec(f, list(args or []), stack)

    def _exec(self, f: Func, args: List[Any], stack: List[Any]):
        code = f.code
        consts = f.consts
        names = f.names
        locals_ = list(args) + [None] * (f.nlocals - len(args))
        ip = 0
        n = len(code)
        while ip < n:
            op = code[ip]
            arg = code[ip + 1]
            if op == PUSH:
                stack.append(consts[arg]); ip += 2
            elif op == LOAD:
                name = names[arg]
                v = self.globals.get(name)
                if v is None and name in self.builtins:
                    v = self.builtins[name]
                stack.append(v); ip += 2
            elif op == STORE:
                self.globals[names[arg]] = stack.pop(); ip += 2
            elif op == LLOAD:
                stack.append(locals_[arg]); ip += 2
            elif op == LSTORE:
                if arg >= len(locals_):
                    while len(locals_) <= arg:
                        locals_.append(None)
                locals_[arg] = stack.pop(); ip += 2
            elif op == BINOP:
                b = stack.pop(); a = stack.pop()
                if arg == OP_ADD and (isinstance(a, str) or isinstance(b, str)):
                    stack.append(str(a) + str(b))
                else:
                    stack.append(_BIN[arg](a, b))
                ip += 2
            elif op == UOP:
                v = stack.pop()
                stack.append(-v if arg == OP_NEG else (not bool(v))); ip += 2
            elif op == CMP:
                b = stack.pop(); a = stack.pop()
                if arg == OP_LEN:
                    stack.append(len(a))
                else:
                    stack.append(_CMP[arg](a, b))
                ip += 2
            elif op == JUMP:
                ip = arg
            elif op == JIF:
                v = stack.pop()
                ip = arg if (not bool(v)) else ip + 2
            elif op == JIT:
                v = stack.pop()
                ip = arg if bool(v) else ip + 2
            elif op == DUP:
                stack.append(stack[-1])
                ip += 2
            elif op == POPOP:
                stack.pop()
                ip += 2
            elif op == CALL:
                na = arg
                args_ = [stack.pop() for _ in range(na)][::-1]
                callee = stack.pop()
                stack.append(self._call(callee, args_))
                ip += 2
            elif op == RET:
                return stack.pop() if stack else None
            elif op == MLIST:
                items = [stack.pop() for _ in range(arg)][::-1]
                stack.append(items); ip += 2
            elif op == MAKEDICT:
                d = {}
                for _ in range(arg):
                    v = stack.pop(); k = stack.pop()
                    d[k] = v
                stack.append(d); ip += 2
            elif op == GETITEM:
                idx = stack.pop(); obj = stack.pop()
                try:
                    stack.append(obj[idx])
                except (KeyError, IndexError, TypeError):
                    stack.append(None)
                ip += 2
            elif op == GETATTR:
                obj = stack.pop()
                stack.append(_getattr(obj, names[arg]))
                ip += 2
            elif op == ATTRSET:
                val = stack.pop()
                obj = stack.pop()
                if not isinstance(obj, StructInstance):
                    raise RuntimeError("属性赋值只支持 struct 实例")
                obj.fields[names[arg]] = val
                stack.append(val)
                ip += 2
            else:
                raise RuntimeError(f"未知操作码 {op}")
        return None

    def _call(self, callee, args):
        if isinstance(callee, _StructCtor):
            if len(args) != len(callee.fields):
                raise RuntimeError(
                    f"{callee.name} 需要 {len(callee.fields)} 个字段"
                    f"({', '.join(callee.fields)})，实际给了 {len(args)} 个")
            methods = {}
            for key, val in self.globals.items():
                if isinstance(key, str) and key.startswith(callee.name + "."):
                    methods[key.split(".", 1)[1]] = val
            return StructInstance(callee.name, dict(zip(callee.fields, args)), methods)
        if isinstance(callee, BoundMethod):
            fn = callee.target.methods.get(callee.name.split(".", 1)[1])
            if isinstance(fn, _UserFunc):
                return self._exec(fn.func, [callee.target] + list(args), [])
            raise RuntimeError(f"无法调用方法 {callee.name}")
        if isinstance(callee, _UserFunc):
            f = callee.func
            return self._exec(f, args, [])
        if isinstance(callee, InteropValue):
            try:
                return callee.bind_call(args)
            except Exception as e:
                raise RuntimeError(f"互操作调用失败 {callee!r}: {e}")
        if callable(callee):
            return callee(*args)
        raise RuntimeError(f"无法调用 {callee!r}")


def _getattr(obj, name):
    if isinstance(obj, StructInstance):
        if name in obj.fields:
            return obj.fields[name]
        if name in obj.methods:
            return BoundMethod(f"{obj.type_name}.{name}", obj)
        raise RuntimeError(f"{obj.type_name} 没有字段/方法 {name!r}")
    if isinstance(obj, InteropValue):
        child = obj.bind_getattr(name)
        if child is None:
            raise RuntimeError(f"互操作对象无成员 {name!r}")
        return child
    if isinstance(obj, list):
        if name == "len":
            return lambda: len(obj)
        if name == "sum":
            return lambda: sum(obj)
        if name == "append":
            return lambda x: obj.append(x)
    if hasattr(obj, name):
        return getattr(obj, name)
    raise RuntimeError(f"对象无成员 {name!r}")


def run_vm(program, builtins=None, interop=None):
    if builtins is None:
        from .evaluator import _BUILTINS
        builtins = {k: v[0] for k, v in _BUILTINS.items()}
    uses = [e for e in program.entries if isinstance(e, Use)]
    if uses and interop is None:
        from .interop import InteropBus
        interop = InteropBus()
    compiler = Compiler(program)
    funcs = compiler.compile()
    vm = VM(funcs, builtins=builtins, interop=interop, uses=uses,
            structs=getattr(compiler, "structs", {}))
    return vm.run()

def _collect_let_names(stmts) -> List[str]:
    """递归收集语句块中所有 `let` 声明的变量名（保持首次出现顺序）。"""
    found: List[str] = []
    seen = set()

    def walk(ss):
        for st in ss or []:
            if isinstance(st, Assign):
                if getattr(st, "is_decl", False) and st.name not in seen:
                    seen.add(st.name)
                    found.append(st.name)
            elif isinstance(st, If):
                for _, b in st.branches:
                    walk(b)
                walk(st.else_body)
            elif isinstance(st, While):
                walk(st.body)
            elif isinstance(st, ForIn):
                walk(st.body)
            elif isinstance(st, Sandbox):
                walk(st.body)

    walk(stmts)
    return found
