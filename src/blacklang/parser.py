# -*- coding: utf-8 -*-
"""BlackLang 递归下降解析器：Tokens -> AST。"""
from __future__ import annotations

from typing import List

from .ast_nodes import (
    Assign, Binary, Call, Compare, DictLit, Expr, ExprStmt, ForIn, FuncDef,
    GetAttr, GetItem, If, ListLit, Literal, Name, Program, Return, Sandbox,
    Stmt, Unary, Use, While,
)
from .tokenizer import Token, tokenize


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    # ---- helpers ----
    def _cur(self) -> Token:
        return self.tokens[self.pos]

    def _peek(self, offset: int = 1) -> Token:
        idx = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[idx]

    def _check(self, kind: str, value: str | None = None) -> bool:
        t = self._cur()
        if t.kind != kind:
            return False
        return value is None or t.value == value

    def _advance(self) -> Token:
        t = self.tokens[self.pos]
        if t.kind != "EOF":
            self.pos += 1
        return t

    def _expect(self, kind: str, value: str | None = None) -> Token:
        if not self._check(kind, value):
            t = self._cur()
            want = value or kind
            raise ParseError(f"{t.line}:{t.col} 期望 {want!r}，但得到 {t.value!r}")
        return self._advance()

    def _expect_ident(self) -> str:
        t = self._expect("IDENT")
        return t.value

    # ---- program ----
    def parse_program(self) -> Program:
        entries: List[Stmt] = []
        while not self._check("EOF"):
            entries.append(self._stmt())
        return Program(entries=entries)

    # ---- statements ----
    def _stmt(self) -> Stmt:
        t = self._cur()
        if t.kind == "KEYWORD":
            k = t.value
            if k == "let":
                return self._assign()
            if k == "if":
                return self._if()
            if k == "while":
                return self._while()
            if k == "for":
                return self._for()
            if k == "return":
                return self._return()
            if k == "fn":
                return self._fn()
            if k == "sandbox":
                return self._sandbox()
            if k == "use":
                return self._use()
            if k == "run":
                return self._run_main()
            if k == "par":
                raise ParseError(f"{t.line}:{t.col} par 在 MVP 中暂未实现")
        # 赋值语句：IDENT = expr  (重新赋值)
        if t.kind == "IDENT" and self._peek().kind == "OP" and self._peek().value == "=":
            name = self._advance().value
            self._expect("OP", "=")
            value = self._expr()
            if self._check("OP", ";"):
                self._advance()
            return Assign(name=name, value=value)
        # 否则是表达式语句
        expr = self._expr()
        # 分号可省略：遇到 } 或块关键字即视为语句结束（AI 友好）
        if self._check("OP", ";"):
            self._advance()
        return ExprStmt(expr)

    def _assign(self) -> Assign:
        self._expect("KEYWORD", "let")
        name = self._expect_ident()
        type_annotation = None
        if self._check("OP", ":"):
            self._advance()
            type_annotation = self._advance().value
        self._expect("OP", "=")
        value = self._expr()
        if self._check("OP", ";"):
            self._advance()
        return Assign(name=name, value=value, type_annotation=type_annotation)

    def _if(self) -> If:
        self._expect("KEYWORD", "if")
        cond = self._expr()
        if self._check("OP", ":"):
            self._advance()
        body = self._block()
        branches = [(cond, body)]
        while self._check("KEYWORD", "elif"):
            self._advance()
            c = self._expr()
            if self._check("OP", ":"):
                self._advance()
            b = self._block()
            branches.append((c, b))
        else_body = None
        if self._check("KEYWORD", "else"):
            self._advance()
            if self._check("OP", ":"):
                self._advance()
            else_body = self._block()
        return If(branches=branches, else_body=else_body)

    def _while(self) -> While:
        self._expect("KEYWORD", "while")
        cond = self._expr()
        if self._check("OP", ":"):
            self._advance()
        body = self._block()
        return While(cond=cond, body=body)

    def _for(self) -> ForIn:
        self._expect("KEYWORD", "for")
        var = self._expect_ident()
        self._expect("KEYWORD", "in")
        it = self._expr()
        if self._check("OP", ":"):
            self._advance()
        body = self._block()
        return ForIn(var=var, iterable=it, body=body)

    def _return(self) -> Return:
        self._expect("KEYWORD", "return")
        if self._check("OP", ";"):
            self._advance()
            return Return(value=None)
        value = self._expr()
        if self._check("OP", ";"):
            self._advance()
        return Return(value=value)

    def _fn(self) -> FuncDef:
        self._expect("KEYWORD", "fn")
        name = self._expect_ident()
        self._expect("OP", "(")
        params: List[str] = []
        param_types: List[Optional[str]] = []
        if not self._check("OP", ")"):
            p = self._expect_ident()
            params.append(p)
            t = None
            if self._check("OP", ":"):
                self._advance()
                t = self._expect_ident()
            param_types.append(t)
            while self._check("OP", ","):
                self._advance()
                p = self._expect_ident()
                params.append(p)
                t = None
                if self._check("OP", ":"):
                    self._advance()
                    t = self._expect_ident()
                param_types.append(t)
        self._expect("OP", ")")
        return_type = None
        if self._check("OP", "->"):
            self._advance()
            return_type = self._expect_ident()
        if self._check("OP", ":"):
            self._advance()
        body = self._block()
        return FuncDef(name=name, params=params, param_types=param_types,
                       return_type=return_type, body=body)

    def _sandbox(self) -> Sandbox:
        self._expect("KEYWORD", "sandbox")
        caps: List[str] = []
        # 读取一个或多个能力名；支持连字符名如 readonly-fs / write-fs
        while not self._check("OP", ":"):
            if self._cur().kind not in ("IDENT", "KEYWORD"):
                break
            name = self._advance().value
            while (self._check("OP", "-")
                   and self._peek().kind in ("IDENT", "KEYWORD")):
                self._advance()  # 吃掉 '-'
                name += "-" + self._advance().value
            caps.append(name)
        self._expect("OP", ":")
        body = self._block()
        return Sandbox(caps=caps, body=body)

    def _use(self) -> Use:
        self._expect("KEYWORD", "use")
        lang = self._expect_ident()
        self._expect("OP", ":")
        self._expect("OP", "{")
        names: List[str] = []
        if not self._check("OP", "}"):
            names.append(self._expect_ident())
            while self._check("OP", ","):
                self._advance()
                names.append(self._expect_ident())
        self._expect("OP", "}")
        if self._check("OP", ";"):
            self._advance()
        return Use(lang=lang, names=names)

    def _run_main(self) -> Stmt:
        # run main : { ... }  —— 限定全局属性；MVP 中把它当普通块执行
        self._expect("KEYWORD", "run")
        self._expect("KEYWORD", "main")
        self._expect("OP", ":")
        body = self._block()
        # 用花括号块本身表达 —— 转成一个 Immediately-Invoked-like 顺序块
        # 这里直接返回一个 ExprStmt 占位不合理，返回一个内联块：用 If(True) 近似。
        return If(branches=[(Literal(True), body)], else_body=None)

    def _block(self) -> List[Stmt]:
        self._expect("OP", "{")
        stmts: List[Stmt] = []
        while not self._check("OP", "}"):
            if self._check("EOF"):
                raise ParseError("块未闭合：缺少 }}")
            stmts.append(self._stmt())
        self._expect("OP", "}")
        return stmts

    # ---- expressions (precedence climbing) ----
    def _expr(self) -> Expr:
        return self._or()

    def _or(self) -> Expr:
        left = self._and()
        while self._check("OP", "||") or self._check("KEYWORD", "or"):
            self._advance()
            right = self._and()
            left = Binary("or", left, right)
        return left

    def _and(self) -> Expr:
        left = self._equality()
        while self._check("OP", "&&") or self._check("KEYWORD", "and"):
            self._advance()
            right = self._equality()
            left = Binary("and", left, right)
        return left

    def _equality(self) -> Expr:
        left = self._comparison()
        while self._check("OP", "==") or self._check("OP", "!="):
            op = self._advance().value
            right = self._comparison()
            # 统一为 Compare 节点，操作符用规范的 '=='/'!='，与 < > <= >= 一致
            left = Compare(op=op, left=left, right=right)
        return left

    def _comparison(self) -> Expr:
        left = self._term()
        while self._cur().kind == "OP" and self._cur().value in ("<", ">", "<=", ">="):
            op = self._advance().value
            right = self._term()
            left = Compare(op=op, left=left, right=right)
        return left

    def _term(self) -> Expr:
        left = self._factor()
        while self._cur().kind == "OP" and self._cur().value in ("+", "-"):
            op = self._advance().value
            right = self._factor()
            left = Binary(op, left, right)
        return left

    def _factor(self) -> Expr:
        left = self._unary()
        while self._cur().kind == "OP" and self._cur().value in ("*", "/", "%"):
            op = self._advance().value
            right = self._unary()
            left = Binary(op, left, right)
        return left

    def _unary(self) -> Expr:
        if self._check("OP", "-") or self._check("OP", "!") or self._check("KEYWORD", "not"):
            op = self._advance().value
            operand = self._unary()
            if op == "-":
                return Unary("-", operand)
            return Unary("!", operand)
        return self._postfix()

    def _postfix(self) -> Expr:
        expr = self._primary()
        while True:
            if self._check("OP", "("):
                expr = self._call_on(expr)
            elif self._check("OP", "."):
                self._advance()
                attr = self._expect_ident()
                expr = GetAttr(obj=expr, attr=attr)
            elif self._check("OP", "["):
                self._advance()
                idx = self._expr()
                self._expect("OP", "]")
                expr = GetItem(obj=expr, index=idx)
            else:
                break
        return expr

    def _call_on(self, callee: Expr) -> Call:
        self._expect("OP", "(")
        args: List[Expr] = []
        if not self._check("OP", ")"):
            args.append(self._expr())
            while self._check("OP", ","):
                self._advance()
                args.append(self._expr())
        self._expect("OP", ")")
        return Call(callee=callee, args=args)

    def _primary(self) -> Expr:
        t = self._cur()
        if t.kind == "NUMBER":
            self._advance()
            if "." in t.value:
                return Literal(float(t.value))
            return Literal(int(t.value))
        if t.kind == "STRING":
            self._advance()
            return Literal(t.value)
        if t.kind == "KEYWORD":
            if t.value == "true":
                self._advance()
                return Literal(True)
            if t.value == "false":
                self._advance()
                return Literal(False)
            # 非控制流关键字（print/len/type/read…）在表达式中作为可调用名称
            if t.value not in ("if", "for", "while", "fn", "return", "and", "or",
                               "not", "sandbox", "use", "run", "let", "main", "in", "par", "import"):
                self._advance()
                return Name(name=t.value)
        if t.kind == "IDENT":
            self._advance()
            # 列表字面量 [1,2,3] 以 ident 不会出现，单独处理 '[' primary
            return Name(name=t.value)
        if self._check("OP", "("):
            self._advance()
            e = self._expr()
            self._expect("OP", ")")
            return e
        if self._check("OP", "["):
            return self._list_literal()
        if self._check("OP", "{"):
            return self._dict_literal()
        raise ParseError(f"{t.line}:{t.col} 无法解析的表达: {t.value!r}")

    def _list_literal(self) -> ListLit:
        self._expect("OP", "[")
        items: List[Expr] = []
        if not self._check("OP", "]"):
            items.append(self._expr())
            while self._check("OP", ","):
                self._advance()
                if self._check("OP", "]"):
                    break
                items.append(self._expr())
        self._expect("OP", "]")
        return ListLit(items=items)

    def _dict_literal(self):
        from .ast_nodes import DictLit
        self._expect("OP", "{")
        pairs = []
        if not self._check("OP", "}"):
            k = self._expr()
            self._expect("OP", ":")
            v = self._expr()
            pairs.append((k, v))
            while self._check("OP", ","):
                self._advance()
                if self._check("OP", "}"):
                    break
                k = self._expr()
                self._expect("OP", ":")
                v = self._expr()
                pairs.append((k, v))
        self._expect("OP", "}")
        return DictLit(pairs=pairs)


def parse(source: str) -> Program:
    tokens = tokenize(source)
    return Parser(tokens).parse_program()