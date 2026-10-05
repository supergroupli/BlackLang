# -*- coding: utf-8 -*-
"""BlackLang 树遍历求值器（MVP）。

在存在质上示范 BlackLang 的三大支柱：
- 快速：解释执行（后续由 AOT/JIT 替换）
- 安全：sandbox 权限在求值期强制（能力即运行时可调用权限）
- 互通：use python 之类的互操作入口（MVP 中为占位桩）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .ast_nodes import (
    Assign, Binary, Call, Compare, DictLit, ExprStmt, ForIn, FuncDef, GetAttr,
    GetItem, If, ListLit, Literal, Name, Program, Return, Sandbox, Unary, Use,
    While,
)
from .interop import InteropBus, InteropError, InteropValue


class ReturnSignal(Exception):
    def __init__(self, value: Any = None):
        self.value = value


class BlackRuntimeError(Exception):
    pass


# ---- 可用的内建函数（网络/文件 I/O 需要对应权限才会被允许） ----
_BUILTINS: Dict[str, tuple] = {
    # name -> (func, required_cap, doc)
    "print": (lambda *a: print(*a), None, "打印到标准输出"),
    "read": (lambda p: open(p, encoding="utf-8").read(), "readonly-fs", "读取文件内容"),
    "write": (lambda p, s: open(p, "w", encoding="utf-8").write(s), "write-fs", "写入文件内容"),
    "http_get": (lambda url: f"(mock GET {url})", "net", "发起 HTTP GET（MVP 占位）"),
    "http_post": (lambda url, payload: f"(mock POST {url}: {payload})", "net", "发起 HTTP POST（MVP 占位）"),
    "type": (lambda v: type(v).__name__, None, "返回值的类型名"),
    "len": (lambda v: len(v), None, "返回长度"),
    "str": (lambda v: str(v), None, "转为字符串"),
    "int": (lambda v: int(v), None, "转为整数"),
    "float": (lambda v: float(v), None, "转为浮点"),
    "append": (lambda v, x: v.append(x), None, "追加到列表"),
}


def _make_env(outer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    env: Dict[str, Any] = {}
    if outer is not None:
        env.update(outer)
    # 内建函数始终可用（但受当前 sandbox 的 cap 约束）
    env["__builtins__"] = _BUILTINS
    return env


class Evaluator:
    def __init__(self, program: Program):
        self.program = program
        self.builtins = _BUILTINS
        self.caps: set = set()          # 当前累积的权限（运行期）
        self.bus = InteropBus()         # Interop Bus：与宿主语言互操作
        self.globals: Dict[str, Any] = _make_env()

    # ---- 顶层执行 ----
    def run(self) -> Any:
        for stmt in self.program.entries:
            if isinstance(stmt, Use):
                self._exec_use(stmt)
            elif isinstance(stmt, FuncDef):
                self._define(stmt)
            else:
                self._exec(stmt)
        return None

    # ---- 语句 ----
    def _exec(self, stmt) -> None:
        if isinstance(stmt, Assign):
            self.globals[stmt.name] = self._eval(stmt.value)
        elif isinstance(stmt, ExprStmt):
            self._eval(stmt.expr)
        elif isinstance(stmt, If):
            for cond, body in stmt.branches:
                if self._truthy(self._eval(cond)):
                    self._exec_block(body, self.globals)
                    return
            if stmt.else_body:
                self._exec_block(stmt.else_body, self.globals)
        elif isinstance(stmt, While):
            guard = 0
            while self._truthy(self._eval(stmt.cond)):
                self._exec_block(stmt.body, self.globals)
                guard += 1
                if guard > 1_000_000:
                    raise BlackRuntimeError("循环次数超过安全上限")
        elif isinstance(stmt, ForIn):
            it = self._eval(stmt.iterable)
            for item in it:
                self.globals[stmt.var] = item
                self._exec_block(stmt.body, self.globals)
        elif isinstance(stmt, Return):
            raise ReturnSignal(self._eval(stmt.value) if stmt.value else None)
        elif isinstance(stmt, Sandbox):
            self._exec_sandbox(stmt)
        elif isinstance(stmt, Use):
            self._exec_use(stmt)
        elif isinstance(stmt, FuncDef):
            self._define(stmt)
        else:
            raise BlackRuntimeError(f"暂不支持的语句: {type(stmt).__name__}")

    def _exec_block(self, stmts: List, env: Dict[str, Any]) -> None:
        # MVP 简化：函数调用时用局部 env；块语句共享全局作用域
        for s in stmts:
            self._exec(s)

    def _define(self, node: FuncDef) -> None:
        # 先占位，再捕获闭包，使递归函数可见自身
        self.globals[node.name] = {"params": node.params, "body": node.body, "closure": {}}
        self.globals[node.name]["closure"] = dict(self.globals)

    def _exec_sandbox(self, node: Sandbox) -> None:
        saved = set(self.caps)
        self.caps = saved | set(node.caps)
        try:
            self._exec_block(node.body, self.globals)
        finally:
            self.caps = saved

    def _exec_use(self, node: Use) -> None:
        # 通过 Interop Bus 真实绑定宿主语言的能力（如 python 模块）
        try:
            self.bus.bind(node.lang, node.names)
        except InteropError as e:
            raise BlackRuntimeError(str(e))
        for name in node.names:
            if node.lang == "python":
                proxy = self.bus.resolve_python(name)
            elif node.lang == "node":
                proxy = self.bus.resolve_node(name)
            else:
                proxy = None
            if proxy is not None:
                self.globals[name] = proxy
        # 绑定提示打到 stderr，避免污染程序 stdout 输出
        import sys
        print(f"[use] 已绑定 {node.lang}: {', '.join(node.names)}", file=sys.stderr)

    # ---- 表达式 ----
    def _eval(self, node) -> Any:
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, Name):
            return self._lookup(node.name)
        if isinstance(node, GetAttr):
            return self._eval_getattr(node)
        if isinstance(node, GetItem):
            return self._eval_getitem(node)
        if isinstance(node, Binary):
            return self._eval_binary(node)
        if isinstance(node, Unary):
            return self._eval_unary(node)
        if isinstance(node, Compare):
            return self._eval_compare(node)
        if isinstance(node, Call):
            return self._eval_call(node)
        if isinstance(node, ListLit):
            return [self._eval(i) for i in node.items]
        if isinstance(node, DictLit):
            return {self._eval(k): self._eval(v) for k, v in node.pairs}
        raise BlackRuntimeError(f"暂不支持的表达式: {type(node).__name__}")

    def _lookup(self, name: str) -> Any:
        if name in self.globals:
            return self.globals[name]
        raise BlackRuntimeError(f"未定义的名称: {name}")

    def _eval_getitem(self, node: GetItem) -> Any:
        obj = self._eval(node.obj)
        idx = self._eval(node.index)
        if isinstance(obj, InteropValue):
            child = obj.bind_getitem(idx)
            if child is None:
                raise BlackRuntimeError(f"互操作对象 {obj!r} 无索引 {idx!r}")
            return child
        try:
            return obj[idx]
        except (TypeError, KeyError, IndexError) as e:
            raise BlackRuntimeError(f"索引错误: {e}")

    def _eval_getattr(self, node: GetAttr) -> Any:
        obj = self._eval(node.obj)
        if isinstance(obj, InteropValue):
            child = obj.bind_getattr(node.attr)
            if child is None:
                raise BlackRuntimeError(f"互操作对象 {obj!r} 没有成员 {node.attr!r}")
            return child
        # 原生 BlackLang list，支持基本成员
        if isinstance(obj, list):
            methods = {"len": lambda: len(obj), "append": lambda x: obj.append(x),
                       "sum": lambda: sum(obj)}
            if node.attr in methods:
                return methods[node.attr]
        if hasattr(obj, node.attr):
            return getattr(obj, node.attr)
        raise BlackRuntimeError(f"对象没有成员 {node.attr!r}")

    def _eval_binary(self, node: Binary) -> Any:
        op = node.op
        if op in ("and", "or"):
            left = self._eval(node.left)
            if op == "and":
                return left if not self._truthy(left) else self._eval(node.right)
            return left if self._truthy(left) else self._eval(node.right)
        left = self._eval(node.left)
        right = self._eval(node.right)
        if op == "+":
            if isinstance(left, str) or isinstance(right, str):
                return str(left) + str(right)
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right
        if op == "%":
            return left % right
        raise BlackRuntimeError(f"未知二元运算符: {op}")

    def _eval_unary(self, node: Unary) -> Any:
        v = self._eval(node.operand)
        if node.op == "-":
            return -v
        if node.op == "!":
            return not self._truthy(v)
        raise BlackRuntimeError(f"未知一元运算符: {node.op}")

    def _eval_compare(self, node: Compare) -> Any:
        left = self._eval(node.left)
        right = self._eval(node.right)
        mapping = {
            "<": lambda: left < right,
            ">": lambda: left > right,
            "<=": lambda: left <= right,
            ">=": lambda: left >= right,
            "==": lambda: left == right,
            "!=": lambda: left != right,
        }
        if node.op not in mapping:
            raise BlackRuntimeError(f"未知比较运算符: {node.op}")
        return mapping[node.op]()

    def _eval_call(self, node: Call) -> Any:
        args = [self._eval(a) for a in node.args]
        # 内建函数：先于求值 callee（内建不在全局变量表中）
        if isinstance(node.callee, Name) and node.callee.name in self.builtins:
            func, cap, _ = self.builtins[node.callee.name]
            self._require(cap, node.callee.name)
            return func(*args)
        callee = self._eval(node.callee)
        if isinstance(callee, InteropValue):
            try:
                return callee.bind_call(args)
            except Exception as e:
                raise BlackRuntimeError(f"互操作调用失败 {callee!r}: {e}")
        if isinstance(callee, dict) and "params" in callee:
            return self._call_user_fn(callee, args)
        if callable(callee):
            return callee(*args)
        raise BlackRuntimeError(f"无法调用: {callee!r}")

    def _call_user_fn(self, fn: Dict, args: List[Any]) -> Any:
        params = fn["params"]
        if len(params) != len(args):
            raise BlackRuntimeError(
                f"函数 {fn.get('__name__', '?')} 需要 {len(params)} 个参数，得到 {len(args)}"
            )
        local = dict(fn["closure"])
        for p, a in zip(params, args):
            local[p] = a
        old_globals = self.globals
        self.globals = local
        try:
            self._exec_block(fn["body"], local)
            return None
        except ReturnSignal as r:
            return r.value
        finally:
            self.globals = old_globals

    def _require(self, cap: Optional[str], name: str) -> None:
        if cap is None:
            return
        if cap not in self.caps:
            raise BlackRuntimeError(
                f"越权调用: {name} 需要权限 {cap!r}，但当前 sandbox 未授予。"
            )

    @staticmethod
    def _truthy(v: Any) -> bool:
        return bool(v)


def run_program(source: str) -> Any:
    from .parser import parse
    program = parse(source)
    return Evaluator(program).run()