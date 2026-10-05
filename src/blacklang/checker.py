# -*- coding: utf-8 -*-
"""BlackLang 静态检查器（Phase 2a）。

核心价值：把「越权 / 不安全」从运行期崩溃**提前到编译期拒绝**——
这是 BlackLang「面向 AI 的安全」的灵魂，也是它区别于普通脚本语言的关键。

- 类型粗查      —— 提示明显错误（可选，MVP 先聚焦能力分析）
- 能力分析      —— 静态扫描 AST：
    * 任何需要权限的内建调用（net / readonly-fs / write-fs）出现在未授权的
      sandbox 作用域内 -> 编译期直接报错；
    * 自动生成「能力清单」（谁碰了网络 / 谁读了文件），供 AI 声明与审计。

一个函数体实际需要哪些能力，也一并收集（per_fn），用于后续「一次审核，永远复用」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .ast_nodes import (
    Assign, Binary, Call, Compare, ExprStmt, ForIn, FuncDef, GetAttr, GetItem,
    If, ListLit, Literal, Name, Program, Return, Sandbox, Unary, Use, While,
)

# 内建能力要求表：函数名 -> 所需权限（None 表示无需权限）
_CAP_REQUIREMENTS: Dict[str, Optional[str]] = {
    "print": None,
    "read": "readonly-fs",
    "write": "write-fs",
    "http_get": "net",
    "http_post": "net",
    "type": None,
    "len": None,
}


@dataclass
class CapabilityManifest:
    program_caps: List[str] = field(default_factory=list)
    per_fn: Dict[str, List[str]] = field(default_factory=dict)
    unauthorized: List[str] = field(default_factory=list)


class StaticChecker:
    def __init__(self, program: Program):
        self.program = program
        self.errors: List[str] = []
        self.manifest = CapabilityManifest()

    # ---- 公开入口 ----
    def check(self) -> List[str]:
        program_caps: Set[str] = set()
        for e in self.program.entries:
            program_caps |= self._collect_caps(e)
            self._scan_stmt(e, granted=set())
        self.manifest.program_caps = sorted(program_caps)
        return self.errors

    # ---- 语句扫描（带授权上下文） ----
    def _scan_stmt(self, stmt, granted: Set[str]):
        if isinstance(stmt, Assign):
            self._scan_expr(stmt.value, granted)
        elif isinstance(stmt, If):
            for cond, body in stmt.branches:
                self._scan_expr(cond, granted)
                self._scan_body(body, granted)
            if stmt.else_body:
                self._scan_body(stmt.else_body, granted)
        elif isinstance(stmt, While):
            self._scan_expr(stmt.cond, granted)
            self._scan_body(stmt.body, granted)
        elif isinstance(stmt, ForIn):
            self._scan_expr(stmt.iterable, granted)
            self._scan_body(stmt.body, granted)
        elif isinstance(stmt, Return):
            if stmt.value:
                self._scan_expr(stmt.value, granted)
        elif isinstance(stmt, Sandbox):
            # sandbox 扩大授权
            self._scan_body(stmt.body, granted | set(stmt.caps))
        elif isinstance(stmt, FuncDef):
            self._scan_fn(stmt)
        elif isinstance(stmt, ExprStmt):
            self._scan_expr(stmt.expr, granted)

    def _scan_body(self, body, granted: Set[str]):
        for s in body:
            self._scan_stmt(s, granted)

    def _scan_fn(self, node: FuncDef):
        """函数体默认零权限，sandbox 内可扩大。收集该函数所需能力到 per_fn。"""
        self._scan_body(node.body, granted=set())
        self.manifest.per_fn[node.name] = sorted(self._collect_caps(node))

    # ---- 表达式扫描 + 编译期越权拒绝 ----
    def _scan_expr(self, node, granted: Set[str]):
        if isinstance(node, (Literal, Name)):
            return
        if isinstance(node, (Binary, Compare)):
            self._scan_expr(node.left, granted)
            self._scan_expr(node.right, granted)
        elif isinstance(node, Unary):
            self._scan_expr(node.operand, granted)
        elif isinstance(node, ListLit):
            for i in node.items:
                self._scan_expr(i, granted)
        elif isinstance(node, GetAttr):
            self._scan_expr(node.obj, granted)
        elif isinstance(node, GetItem):
            self._scan_expr(node.obj, granted)
            self._scan_expr(node.index, granted)
        elif isinstance(node, Call):
            self._scan_call(node, granted)

    def _scan_call(self, node: Call, granted: Set[str]):
        for a in node.args:
            self._scan_expr(a, granted)
        if isinstance(node.callee, Name) and node.callee.name in _CAP_REQUIREMENTS:
            need = _CAP_REQUIREMENTS[node.callee.name]
            if need and need not in granted:
                self.errors.append(
                    f"编译期越权拒绝: {node.callee.name} 需要权限 {need!r}，"
                    f"但当前作用域未授予（请置于 sandbox {need}: {{ ... }} 内）"
                )
                self.manifest.unauthorized.append(node.callee.name)

    # ---- 能力收集（与授权无关，用于生成 manifest） ----
    def _collect_caps(self, node) -> Set[str]:
        caps: Set[str] = set()
        if isinstance(node, Call):
            if isinstance(node.callee, Name) and node.callee.name in _CAP_REQUIREMENTS:
                need = _CAP_REQUIREMENTS[node.callee.name]
                if need:
                    caps.add(need)
            for a in node.args:
                caps |= self._collect_caps(a)
        elif isinstance(node, Assign):
            caps |= self._collect_caps(node.value)
        elif isinstance(node, ExprStmt):
            caps |= self._collect_caps(node.expr)
        elif isinstance(node, If):
            for cond, b in node.branches:
                caps |= self._collect_caps(cond)
                for s in b:
                    caps |= self._collect_caps(s)
            if node.else_body:
                for s in node.else_body:
                    caps |= self._collect_caps(s)
        elif isinstance(node, While):
            caps |= self._collect_caps(node.cond)
            for s in node.body:
                caps |= self._collect_caps(s)
        elif isinstance(node, ForIn):
            caps |= self._collect_caps(node.iterable)
            for s in node.body:
                caps |= self._collect_caps(s)
        elif isinstance(node, Return):
            if node.value:
                caps |= self._collect_caps(node.value)
        elif isinstance(node, Sandbox):
            for s in node.body:
                caps |= self._collect_caps(s)
        elif isinstance(node, FuncDef):
            for s in node.body:
                caps |= self._collect_caps(s)
        elif isinstance(node, Unary):
            caps |= self._collect_caps(node.operand)
        elif isinstance(node, GetAttr):
            caps |= self._collect_caps(node.obj)
        elif isinstance(node, GetItem):
            caps |= self._collect_caps(node.obj)
            caps |= self._collect_caps(node.index)
        elif isinstance(node, (Binary, Compare)):
            caps |= self._collect_caps(node.left)
            caps |= self._collect_caps(node.right)
        elif isinstance(node, ListLit):
            for i in node.items:
                caps |= self._collect_caps(i)
        return caps


def static_check(program: Program):
    """便捷入口：返回 (checker, errors)。"""
    c = StaticChecker(program)
    errors = c.check()
    return c, errors