# -*- coding: utf-8 -*-
"""BlackLang 标准库（字符串 / 列表 / 字典 方法 + 全局函数）。

设计原则（面向 AI）：
- **名字直白**：`upper` / `trim` / `split` / `index_of` / `to_int`，少猜；
- **同一实现**：VM 与树遍历求值器共用本模块，保证两路输出逐字节一致；
- **可回退**：C/LLVM 后端支持部分（数值函数 + 标量字符串方法），其余明确诊断并回退。

用法（语言内）：
    let s = " a,b "
    print(s.trim().upper())          // "A,B"
    print(s.trim().split(","))       // ["a", "b"]
    print([3,1,2].sort())            // [1, 2, 3]
    print(abs(-3) + max(1, 2))       // 5
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional


class StdlibError(Exception):
    """标准库调用错误（参数个数/类型不符）。"""


class NativeMethod:
    """绑定到某个对象的标准库方法：`s.upper` 取到它，`s.upper()` 调用它。"""

    __slots__ = ("obj", "name", "_fn")

    def __init__(self, obj: Any, name: str, fn: Callable):
        self.obj = obj
        self.name = name
        self._fn = fn

    def __call__(self, *args):
        try:
            return self._fn(self.obj, *args)
        except StdlibError:
            raise
        except TypeError as e:
            raise StdlibError(f"{self.name}() 参数不符: {e}") from e

    def __repr__(self):
        return f"<方法 {self.name}>"


# ---------------------------------------------------------------- 字符串方法
def _s_len(s, *_):
    return len(s)


def _s_upper(s, *_):
    return s.upper()


def _s_lower(s, *_):
    return s.lower()


def _s_trim(s, *_):
    return s.strip()


def _s_split(s, sep=None, *_):
    if sep is None or sep == "":
        return s.split()
    return s.split(sep)


def _s_join(s, items, *_):
    return s.join(str(x) for x in items)


def _s_replace(s, a, b, *_):
    return s.replace(str(a), str(b))


def _s_contains(s, sub, *_):
    return str(sub) in s


def _s_starts_with(s, p, *_):
    return s.startswith(str(p))


def _s_ends_with(s, p, *_):
    return s.endswith(str(p))


def _s_index_of(s, sub, *_):
    return s.find(str(sub))


def _s_count(s, sub, *_):
    return s.count(str(sub))


def _s_slice(s, start, end=None, *_):
    if end is None:
        return s[int(start):]
    return s[int(start):int(end)]


def _s_char_at(s, i, *_):
    try:
        return s[int(i)]
    except IndexError:
        raise StdlibError(f"char_at({i}) 越界（长度 {len(s)}）")


def _s_to_int(s, *_):
    try:
        return int(s.strip())
    except ValueError:
        raise StdlibError(f"to_int(): {s!r} 不是合法整数")


def _s_to_float(s, *_):
    try:
        return float(s.strip())
    except ValueError:
        raise StdlibError(f"to_float(): {s!r} 不是合法数字")


def _s_repeat(s, n, *_):
    return s * int(n)


def _s_reverse(s, *_):
    return s[::-1]


def _s_is_empty(s, *_):
    return len(s) == 0


def _s_is_digit(s, *_):
    return s.isdigit()


def _s_pad_left(s, n, ch=" ", *_):
    return s.rjust(int(n), str(ch))


def _s_pad_right(s, n, ch=" ", *_):
    return s.ljust(int(n), str(ch))


_STRING_METHODS: Dict[str, Callable] = {
    "len": _s_len, "upper": _s_upper, "lower": _s_lower, "trim": _s_trim,
    "split": _s_split, "join": _s_join, "replace": _s_replace,
    "contains": _s_contains, "starts_with": _s_starts_with,
    "ends_with": _s_ends_with, "index_of": _s_index_of, "count": _s_count,
    "slice": _s_slice, "char_at": _s_char_at, "to_int": _s_to_int,
    "to_float": _s_to_float, "repeat": _s_repeat, "reverse": _s_reverse,
    "is_empty": _s_is_empty, "is_digit": _s_is_digit,
    "pad_left": _s_pad_left, "pad_right": _s_pad_right,
}


# ---------------------------------------------------------------- 列表方法
def _l_len(x, *_):
    return len(x)


def _l_append(x, v, *_):
    x.append(v)
    return x


def _l_sum(x, *_):
    return sum(x)


def _l_sort(x, *_):
    return sorted(x)


def _l_sort_in_place(x, *_):
    x.sort()
    return x


def _l_reverse(x, *_):
    return list(reversed(x))


def _l_contains(x, v, *_):
    return v in x


def _l_index_of(x, v, *_):
    try:
        return x.index(v)
    except ValueError:
        return -1


def _l_join(x, sep="", *_):
    return str(sep).join(str(i) for i in x)


def _l_slice(x, start, end=None, *_):
    if end is None:
        return list(x[int(start):])
    return list(x[int(start):int(end)])


def _l_max(x, *_):
    return max(x)


def _l_min(x, *_):
    return min(x)


def _l_is_empty(x, *_):
    return len(x) == 0


def _l_first(x, *_):
    return x[0] if x else None


def _l_last(x, *_):
    return x[-1] if x else None


def _l_insert(x, i, v, *_):
    x.insert(int(i), v)
    return x


def _l_remove_at(x, i, *_):
    return x.pop(int(i))


def _l_unique(x, *_):
    out = []
    for v in x:
        if v not in out:
            out.append(v)
    return out


def _l_extend(x, other, *_):
    x.extend(other)
    return x


_LIST_METHODS: Dict[str, Callable] = {
    "len": _l_len, "append": _l_append, "push": _l_append, "sum": _l_sum,
    "sort": _l_sort, "sort_in_place": _l_sort_in_place, "reverse": _l_reverse,
    "contains": _l_contains, "index_of": _l_index_of, "join": _l_join,
    "slice": _l_slice, "max": _l_max, "min": _l_min, "is_empty": _l_is_empty,
    "first": _l_first, "last": _l_last, "insert": _l_insert,
    "remove_at": _l_remove_at, "unique": _l_unique, "extend": _l_extend,
}


# ---------------------------------------------------------------- 字典方法
def _d_len(d, *_):
    return len(d)


def _d_keys(d, *_):
    return list(d.keys())


def _d_values(d, *_):
    return list(d.values())


def _d_has(d, k, *_):
    return k in d


def _d_get(d, k, default=None, *_):
    return d.get(k, default)


def _d_is_empty(d, *_):
    return len(d) == 0


_DICT_METHODS: Dict[str, Callable] = {
    "len": _d_len, "keys": _d_keys, "values": _d_values, "has": _d_has,
    "get": _d_get, "is_empty": _d_is_empty,
}


# ---------------------------------------------------------------- 全局函数
def _g_abs(x):
    return abs(x)


def _g_min(a, b, *rest):
    return min(a, b, *rest)


def _g_max(a, b, *rest):
    return max(a, b, *rest)


def _g_floor(x):
    return math.floor(x)


def _g_ceil(x):
    return math.ceil(x)


def _g_round(x, n=None):
    if n is None:
        return round(x)
    return round(x, int(n))


def _g_sqrt(x):
    return math.sqrt(x)


def _g_pow(a, b):
    # 与 math.pow 一致：恒返回 float，保证各后端一致
    return float(a) ** float(b)


def _g_clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _g_sign(x):
    return (x > 0) - (x < 0)


GLOBAL_FUNCS: Dict[str, Callable] = {
    "abs": _g_abs, "min": _g_min, "max": _g_max, "floor": _g_floor,
    "ceil": _g_ceil, "round": _g_round, "sqrt": _g_sqrt, "pow": _g_pow,
    "clamp": _g_clamp, "sign": _g_sign,
}

# 全局函数的最小参数个数（用于参数校验）
GLOBAL_ARITY = {
    "abs": (1, 1), "min": (2, None), "max": (2, None), "floor": (1, 1),
    "ceil": (1, 1), "round": (1, 2), "sqrt": (1, 1), "pow": (2, 2),
    "clamp": (3, 3), "sign": (1, 1),
}


# ---------------------------------------------------------------- 对外接口
def method_table(obj: Any) -> Optional[Dict[str, Callable]]:
    """按运行时类型选择方法表。"""
    if isinstance(obj, bool):
        return None
    if isinstance(obj, str):
        return _STRING_METHODS
    if isinstance(obj, list):
        return _LIST_METHODS
    if isinstance(obj, dict):
        return _DICT_METHODS
    return None


def get_method(obj: Any, name: str) -> Optional[NativeMethod]:
    """取绑定方法；对象类型不支持或方法名不存在返回 None。"""
    table = method_table(obj)
    if table is None:
        return None
    fn = table.get(name)
    if fn is None:
        return None
    return NativeMethod(obj, name, fn)


def has_method(obj: Any, name: str) -> bool:
    table = method_table(obj)
    return bool(table and name in table)


def is_native_method(v: Any) -> bool:
    return isinstance(v, NativeMethod)


def method_names(obj: Any) -> List[str]:
    table = method_table(obj)
    return sorted(table.keys()) if table else []
