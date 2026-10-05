# -*- coding: utf-8 -*-
"""BlackLang 词法分析器（Token 定义与分词）。

为零依赖实现，使用手写扫描器。支持：
- 数字（整数 / 浮点）
- 标识符（含下划线，字节可以是字母/数字/_）
- 关键字
- 字符串（单引号 ' 与 双引号 "）
- 运算符与分隔符
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

KEYWORDS = {
    "run", "main", "if", "elif", "else", "for", "in", "while",
    "fn", "return", "let", "true", "false", "and", "or", "not",
    "sandbox", "readonly-fs", "net", "par", "use", "struct",
    "print", "import",
}


@dataclass(frozen=True)
class Token:
    kind: str          # 类别：'NUMBER' | 'STRING' | 'IDENT' | 'KEYWORD' | 'OP' | 'EOF'
    value: str
    line: int
    col: int


class LexError(Exception):
    pass


class Tokenizer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1

    def _advance(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        if idx >= len(self.src):
            return ""
        return self.src[idx]

    def _skip_ws_and_comments(self) -> None:
        while self.pos < len(self.src):
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif ch == "/" and self._peek(1) == "/":
                while self.pos < len(self.src) and self._peek() != "\n":
                    self._advance()
            elif ch == "#":
                while self.pos < len(self.src) and self._peek() != "\n":
                    self._advance()
            else:
                break

    def _read_number(self) -> Token:
        line, col = self.line, self.col
        start = self.pos
        is_float = False
        while self.pos < len(self.src) and (self._peek().isdigit() or self._peek() == "_"):
            self._advance()
        if self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            self._advance()
            while self.pos < len(self.src) and (self._peek().isdigit() or self._peek() == "_"):
                self._advance()
        text = self.src[start:self.pos]
        return Token("NUMBER", text, line, col)

    def _read_ident(self) -> Token:
        line, col = self.line, self.col
        start = self.pos
        while self.pos < len(self.src) and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        text = self.src[start:self.pos]
        kind = "KEYWORD" if text in KEYWORDS else "IDENT"
        return Token(kind, text, line, col)

    def _read_string(self) -> Token:
        line, col = self.line, self.col
        quote = self._advance()  # ' or "
        chars = []
        while self.pos < len(self.src):
            ch = self._advance()
            if ch == quote:
                break
            elif ch == "\\":
                if self.pos >= len(self.src):
                    raise LexError(f"{line}:{col} 字符串结尾的转义符无效")
                esc = self._advance()
                mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}
                chars.append(mapping.get(esc, esc))
            else:
                chars.append(ch)
        else:
            raise LexError(f"{line}:{col} 未闭合的字符串")
        return Token("STRING", "".join(chars), line, col)

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        while self.pos < len(self.src):
            self._skip_ws_and_comments()
            if self.pos >= len(self.src):
                break
            ch = self._peek()
            if ch.isdigit():
                tokens.append(self._read_number())
            elif ch.isalpha() or ch == "_":
                tokens.append(self._read_ident())
            elif ch in ("'", '"'):
                tokens.append(self._read_string())
            else:
                line, col = self.line, self.col
                two = self.src[self.pos:self.pos + 2]
                if two in ("==", "!=", "<=", ">=", "&&", "||", "->"):
                    for _ in two:
                        self._advance()
                    tokens.append(Token("OP", two, line, col))
                elif ch in "+-*/%(){}[],:;=<>!&|.":
                    self._advance()
                    tokens.append(Token("OP", ch, line, col))
                else:
                    raise LexError(f"{line}:{col} 无法识别的字符: {ch!r}")
        tokens.append(Token("EOF", "", self.line, self.col))
        return tokens


def tokenize(source: str) -> List[Token]:
    return Tokenizer(source).tokenize()