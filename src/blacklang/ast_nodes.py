# -*- coding: utf-8 -*-
"""BlackLang AST 节点定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


# ---- 表达式 ----
@dataclass
class Expr:
    pass


@dataclass
class Literal(Expr):
    value: Any


@dataclass
class Name(Expr):
    name: str


@dataclass
class GetAttr(Expr):
    obj: Expr
    attr: str


@dataclass
class GetItem(Expr):
    obj: Expr
    index: Expr


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Unary(Expr):
    op: str
    operand: Expr


@dataclass
class Compare(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Call(Expr):
    callee: Expr
    args: List[Expr] = field(default_factory=list)


@dataclass
class ListLit(Expr):
    items: List[Expr] = field(default_factory=list)


@dataclass
class DictLit(Expr):
    pairs: List[tuple] = field(default_factory=list)


# ---- 语句 ----
@dataclass
class Stmt:
    pass


@dataclass
class Assign(Stmt):
    name: str
    value: Expr
    type_annotation: Optional[str] = None   # 可选: int/float/string/bool/list
    is_decl: bool = False                    # True = `let` 声明；False = 重新赋值


@dataclass
class ExprStmt(Stmt):
    expr: Expr


@dataclass
class If(Stmt):
    branches: List[tuple]     # [(cond, body), ...]
    else_body: Optional[List[Stmt]]


@dataclass
class While(Stmt):
    cond: Expr
    body: List[Stmt]


@dataclass
class ForIn(Stmt):
    var: str
    iterable: Expr
    body: List[Stmt]


@dataclass
class Return(Stmt):
    value: Optional[Expr]


@dataclass
class FuncDef(Stmt):
    name: str
    params: List[str]
    body: List[Stmt]
    param_types: List[Optional[str]] = field(default_factory=list)   # 每参数可选类型标注
    return_type: Optional[str] = None                                  # 可选: -> int


@dataclass
class StructDef(Stmt):
    name: str
    fields: List[tuple] = field(default_factory=list)   # [(字段名, 类型名或 None), ...]


@dataclass
class AttrAssign(Stmt):
    obj: Expr
    attr: str
    value: Expr


@dataclass
class Sandbox(Stmt):
    caps: List[str]          # 权限名，如 'readonly-fs', 'net'
    body: List[Stmt]


@dataclass
class Use(Stmt):
    lang: str                # 目标语言，如 'python' / 'node' / 'c'
    names: List[str]         # 导入名称


@dataclass
class Program:
    entries: List[Stmt]      # 顶层语句（其中包含 run main 块）