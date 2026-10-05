# -*- coding: utf-8 -*-
"""BlackLang 静态类型检查器（Phase 2b）。

在 `--check` 时，除「能力分析」外，再做类型推断与检查：
- 从字面量与运算推断表达式类型；
- 检查二元运算、函数调用参数与返回类型是否匹配；
- 类型错误直接编译期暴露，让 AI 生成的类型错误当场可见，而非运行期才炸。

类型：int / float / string / bool / list / any
算术数值提升：int + float -> float
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Union

from .ast_nodes import (
    Assign, Binary, Call, Compare, ExprStmt, ForIn, FuncDef, GetAttr, GetItem,
    If, ListLit, Literal, Name, Program, Return, Sandbox, Unary, Use, While,
)
from .checker import StaticChecker

# 内建函数签名：(参数类型列表, 返回类型)  None 参数类型表示「任意」
BUILTIN_SIGS = {
    "print": ([None], "any"),      # 参数任意，返回无
    "read": (["string"], "string"),
    "write": (["string", "string"], "int"),
    "http_get": (["string"], "string"),
    "http_post": (["string", "any"], "string"),
    "type": ([None], "string"),
    "len": ([None], "int"),
    "str": ([None], "string"),          # 任意值 -> 字符串
    "int": ([None], "int"),
    "float": ([None], "float"),
}

NUMERIC = {"int", "float"}
BOTH_NUM = {"int", "float"}


class TypeErrorBL(Exception):
    pass


class TypeChecker:
    def __init__(self, program: Program, checker: StaticChecker):
        self.program = program
        self.checker = checker          # 复用能力检查器（共享 scope/授权）
        self.errors: List[str] = []
        self.env: Dict[str, str] = {}   # 变量名 -> 类型
        # 预登记内建调用名（避免与用户变量冲突误判）
        self.builtin_names: Set[str] = set(BUILTIN_SIGS)
        self._collect_fn_signatures()

    # ---- 入口 ----
    def check(self) -> List[str]:
        self._check_program()
        return self.errors

    def _collect_fn_signatures(self):
        self.fn_sigs: Dict[str, dict] = {}
        for e in self.program.entries:
            if isinstance(e, FuncDef):
                self.fn_sigs[e.name] = {
                    "param_types": e.param_types,
                    "return_type": e.return_type,
                }

    # ---- 语句 ----
    def _check_program(self):
        for e in self.program.entries:
            if isinstance(e, FuncDef):
                self._check_fn(e)
        # 顶层主体
        for e in self.program.entries:
            if not isinstance(e, FuncDef):
                self._check_stmt(e)

    def _check_fn(self, node: FuncDef):
        saved = dict(self.env)
        for p, t in zip(node.params, node.param_types):
            self.env[p] = t or "any"
        # ① 完整遍历函数体：登记局部 let 声明并检查语句（否则累加器等局部变量会“未定义”）
        self._check_body(node.body)
        # ② 返回类型推断
        inferred = self._check_block_returns(node.body)
        declared = node.return_type
        if declared and inferred and inferred != declared and inferred != "any":
            self._err(f"函数 {node.name} 返回类型不符：声明 {declared}，实际 {inferred}")
        self.env = saved

    def _check_block_returns(self, body) -> Optional[str]:
        # 简化：扫描 return 值类型；冲突时报错
        rt = None
        for s in body:
            if isinstance(s, Return):
                if s.value:
                    t = self._check_expr(s.value)
                    rt = self._merge(rt, t)
                else:
                    rt = self._merge(rt, "void")
            elif isinstance(s, (If, While, ForIn)):
                t = self._scan_nested_returns(s)
                rt = self._merge(rt, t)
            elif isinstance(s, Sandbox):
                t = self._scan_nested_returns(s)
                rt = self._merge(rt, t)
        return rt

    def _scan_nested_returns(self, node) -> Optional[str]:
        rt = None
        if isinstance(node, If):
            for _, b in node.branches:
                rt = self._merge(rt, self._check_block_returns(b))
            if node.else_body:
                rt = self._merge(rt, self._check_block_returns(node.else_body))
        elif isinstance(node, While):
            rt = self._merge(rt, self._check_block_returns(node.body))
        elif isinstance(node, ForIn):
            rt = self._merge(rt, self._check_block_returns(node.body))
        elif isinstance(node, Sandbox):
            rt = self._merge(rt, self._check_block_returns(node.body))
        return rt

    def _merge(self, a, b) -> Optional[str]:
        if a is None:
            return b
        if a == "any":
            return b
        if b is None or b == "any":
            return a
        if a == b:
            return a
        if a in BOTH_NUM and b in BOTH_NUM:
            return "float"
        self._err(f"返回类型冲突：{a} 与 {b}")
        return "any"

    def _check_stmt(self, stmt):
        if isinstance(stmt, Assign):
            t = self._check_expr(stmt.value)
            ann = stmt.type_annotation
            if ann and not self._compat(t, ann):
                self._err(f"变量 {stmt.name} 声明为 {ann}，但初始值为 {t}")
            # 已有赋值后再赋值，检查一致性
            if stmt.name in self.env and self.env[stmt.name] not in ("any",):
                prev = self.env[stmt.name]
                if not self._compat(t, prev):
                    self._err(f"变量 {stmt.name} 已有类型 {prev}，又赋值 {t}")
            self.env[stmt.name] = ann or t
        elif isinstance(stmt, If):
            for cond, body in stmt.branches:
                self._check_expr(cond)
                self._check_body(body)
            if stmt.else_body:
                self._check_body(stmt.else_body)
        elif isinstance(stmt, While):
            self._check_expr(stmt.cond)
            self._check_body(stmt.body)
        elif isinstance(stmt, ForIn):
            et = self._check_expr(stmt.iterable)
            # 推断循环变量类型
            loop_ty = "any"
            if isinstance(stmt.iterable, ListLit) and stmt.iterable.items:
                first = stmt.iterable.items[0]
                loop_ty = self._check_expr(first)
            if stmt.var in self.env and self.env[stmt.var] not in ("any",):
                loop_ty = self.env[stmt.var]
            self.env[stmt.var] = loop_ty
            self._check_body(stmt.body)
        elif isinstance(stmt, Return):
            if stmt.value:
                self._check_expr(stmt.value)
        elif isinstance(stmt, Sandbox):
            self._check_body(stmt.body)
        elif isinstance(stmt, ExprStmt):
            self._check_expr(stmt.expr)
        elif isinstance(stmt, Use):
            # 登记互操作导入：作为不透明 interop 值，io:name
            for n in stmt.names:
                self.env[n] = "any"
        elif isinstance(stmt, FuncDef):
            self._check_fn(stmt)

    def _check_body(self, body):
        for s in body:
            self._check_stmt(s)

    # ---- 表达式 ----
    def _check_expr(self, node) -> str:
        if isinstance(node, Literal):
            return self._literal_type(node.value)
        if isinstance(node, Name):
            if node.name in self.fn_sigs:
                return "fn"
            if node.name in self.builtin_names:
                return "fn"
            t = self.env.get(node.name)
            if t is None:
                self._err(f"类型检查：未定义的名称 {node.name}")
                return "any"
            return t
        if isinstance(node, GetAttr):
            self._check_expr(node.obj)
            return "any"
        if isinstance(node, GetItem):
            self._check_expr(node.obj)
            self._check_expr(node.index)
            return "any"
        if isinstance(node, Binary):
            return self._check_binary(node)
        if isinstance(node, Unary):
            t = self._check_expr(node.operand)
            if node.op == "-" and t not in BOTH_NUM and t != "any":
                self._err(f"一元 '-' 需要数值，实际 {t}")
            if node.op == "!" and t not in ("bool", "any"):
                self._err(f"一元 '!' 需要布尔，实际 {t}")
            return "bool" if node.op == "!" else t
        if isinstance(node, Compare):
            self._check_expr(node.left)
            self._check_expr(node.right)
            return "bool"
        if isinstance(node, Call):
            self._check_expr(node.callee)
            for a in node.args:
                self._check_expr(a)
            return self._call_type(node)
        if isinstance(node, ListLit):
            if node.items:
                first = self._check_expr(node.items[0])
                for it in node.items[1:]:
                    et = self._check_expr(it)
                    if et != "any" and first != "any" and not self._compat(et, first):
                        self._err(f"列表元素类型不一致：{first} 与 {et}")
            else:
                for it in node.items:
                    self._check_expr(it)
            return "list"
        return "any"

    def _literal_type(self, v) -> str:
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "int"
        if isinstance(v, float):
            return "float"
        if isinstance(v, str):
            return "string"
        return "any"

    def _check_binary(self, node: Binary) -> str:
        lt = self._check_expr(node.left)
        rt = self._check_expr(node.right)
        op = node.op
        # 任一侧为 any(未知) 时不报错，类型留待运行期 —— 避免误伤未标注参数
        if lt == "any" or rt == "any":
            return self._num_result(lt, rt)
        if op in ("and", "or"):
            if lt not in ("bool", "any") or rt not in ("bool", "any"):
                self._err(f"逻辑运算符 {op} 需要布尔操作数，得到 {lt} 与 {rt}")
            return "bool"
        # 数值/字符串运算
        if op == "+":
            if lt in BOTH_NUM and rt in BOTH_NUM:
                return "float" if (lt == "float" or rt == "float") else "int"
            # AI 友好：任一侧为 string 时，另一侧自动转 string 拼接（与运行期行为一致）
            if lt == "string" or rt == "string":
                return "string"
            self._err(f"运算符 + 不能用于 {lt} 与 {rt}")
            return "any"
        if op in ("-", "*", "/", "%"):
            if lt not in BOTH_NUM or rt not in BOTH_NUM:
                self._err(f"运算符 {op} 需要数值操作数，得到 {lt} 与 {rt}")
            if op == "/":
                return self._num_result(lt, rt, force_float=True)
            return self._num_result(lt, rt)
        return "any"

    def _num_result(self, a, b, force_float=False):
        if force_float:
            return "float"
        return "float" if (a == "float" or b == "float") else "int"

    def _call_type(self, node: Call) -> str:
        fn = node.callee.name if isinstance(node.callee, Name) else None
        if fn in BUILTIN_SIGS:
            ptypes, rtype = BUILTIN_SIGS[fn]
            # 参数数量
            if len(node.args) != len(ptypes) and not (fn == "print" and len(node.args) >= 1):
                self._err(f"调用 {fn} 期望 {len(ptypes)} 个参数，得到 {len(node.args)}")
            elif fn != "print":
                for arg, expected in zip(node.args, ptypes):
                    at = self._check_expr(arg)
                    if expected and at != "any" and not self._compat(at, expected):
                        self._err(f"调用 {fn} 参数类型应为 {expected}，实际 {at}")
            return rtype
        if fn in self.fn_sigs:
            sig = self.fn_sigs[fn]
            for arg, ep in zip(node.args, sig["param_types"]):
                at = self._check_expr(arg)
                if ep and at != "any" and not self._compat(at, ep):
                    self._err(f"调用 {fn} 参数类型应为 {ep}，实际 {at}")
            return sig["return_type"] or "any"
        return "any"

    def _compat(self, actual: str, declared: str) -> bool:
        if actual == "any" or declared == "any":
            return True
        if actual == declared:
            return True
        if actual in BOTH_NUM and declared in BOTH_NUM:
            return True
        return False

    def _err(self, msg: str):
        self.errors.append(f"类型错误: {msg}")


def type_check(program: Program, checker: StaticChecker) -> List[str]:
    """对已做能力检查的 program 做类型检查，返回类型错误列表。"""
    return TypeChecker(program, checker).check()