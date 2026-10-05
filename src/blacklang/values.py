# -*- coding: utf-8 -*-
"""BlackLang 运行时值类型。

`StructInstance` 是 `struct` 用户自定义类型的实例：

    struct Point { x: int, y: int }
    let p = Point(3, 4)     // 位置构造
    p.x                     // 字段访问
    p.len2()                // 方法（绑定 self）

设计要点（面向 AI / 互操作）：
- 字段可用属性或下标两种方式访问（`p.x` / `p["x"]`），方便与 JSON 互操作；
- 结构可打印为 `Point(x=3, y=4)`，便于 AI 读输出自纠。
"""
from __future__ import annotations

from typing import Any, Dict, List


class StructInstance:
    __slots__ = ("type_name", "fields", "methods")

    def __init__(self, type_name: str, fields: Dict[str, Any],
                 methods: Dict[str, Any] = None):
        self.type_name = type_name
        self.fields = fields
        self.methods = methods or {}

    # 属性 / 下标双访问
    def __getitem__(self, key):
        return self.fields[key]

    def __setitem__(self, key, value):
        self.fields[key] = value

    def __contains__(self, key):
        return key in self.fields

    def keys(self):
        return self.fields.keys()

    def get(self, key, default=None):
        return self.fields.get(key, default)

    def __eq__(self, other):
        if not isinstance(other, StructInstance):
            return NotImplemented
        return self.type_name == other.type_name and self.fields == other.fields

    def __hash__(self):
        return hash((self.type_name, tuple(sorted((k, repr(v)) for k, v in self.fields.items()))))

    def __repr__(self):
        inner = ", ".join(f"{k}={v!r}" for k, v in self.fields.items())
        return f"{self.type_name}({inner})"


class BoundMethod:
    """把方法绑定到某个实例（`p.len2` → 可调用，自动补 self）。"""

    __slots__ = ("name", "target")

    def __init__(self, name: str, target: Any):
        self.name = name
        self.target = target

    def __call__(self, *args):
        raise RuntimeError("BoundMethod 应由求值器调用")

    def __repr__(self):
        return f"<方法 {self.name} of {self.target!r}>"
