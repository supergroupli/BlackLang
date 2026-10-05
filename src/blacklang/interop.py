# -*- coding: utf-8 -*-
"""BlackLang Interop Bus —— 与宿主语言的真正正向互操作（Phase 4）。

MVP 解释器本身用 Python 实现，因此在 Python 宿主内做桥接最直接：
`use python: { numpy }` 会把真实 Python 模块 `numpy` 绑定为 BlackLang 名称，
之后即可在 BlackLang 里调用它（含属性访问、函数调用），并做值转换。

InteropValue 是接口的「不可见封装」：
- 属性访问  -> 返回对应属性/子模块的封装
- 调用      -> 调用 Python 可调用对象，并转换返回值
这就把任意 Python 生态（numpy/pandas/json/os/requests…）直接暴露给 AI。

安全提示：向 Python 宿主开放互操作意味着可执行任意 Python。BlackLang 的
编译期/caps 能力清单会把「use python」标记出来，提示审计；真正的进程级隔离
（沙箱/子进程）属于后续安全强化项，当前为概念落地。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class InteropError(Exception):
    pass


def _to_python(value: Any) -> Any:
    """BlackLang 值 -> Python 原生值。"""
    if isinstance(value, InteropValue):
        return value._obj
    if isinstance(value, list):
        return [_to_python(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_python(v) for k, v in value.items()}
    return value


def _from_python(value: Any) -> Any:
    """Python 原生值 -> BlackLang 值（含递归封装可调用对象）。"""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_from_python(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_python(v) for k, v in value.items()}
    if callable(value) or hasattr(value, "__class__") and not isinstance(value, (str, bytes)):
        # 模块 / 类 / 实例 / 可调用：封装为可点数访问的代理
        return InteropValue(value, _label=type(value).__module__ + "." +
                            getattr(value, "__name__", type(value).__name__))
    return value


class InteropValue:
    """对宿主对象（Python 直连 / Node 远端）的 BlackLang 句柄。

    - Python 模式：持有 `_obj`，所有操作直接透传；
    - Node 模式：持有 `_bridge`（NodeBridge）+ `_handle`，操作走 JSON-RPC。
    """

    def __init__(self, obj: Any = None, _label: str = "python",
                 _bridge: Any = None, _handle: int = None):
        self._obj = obj
        self._label = _label
        self._bridge = _bridge          # Node 模式才有
        self._handle = _handle          # Node 模式的远端句柄 id

    @property
    def _is_remote(self) -> bool:
        return self._bridge is not None

    # ---- 透传 ----
    def __repr__(self) -> str:          # noqa: D105
        return f"<interop {self._label}>"

    def __str__(self) -> str:           # noqa: D105
        return f"<interop {self._label}>"

    def bind_getattr(self, name: str) -> Optional["InteropValue"]:
        """返回名称为 name 的属性/子项句柄；不存在返回 None（由调用方报错）。"""
        if self._is_remote:
            return self._bridge.get_handle_attr(self._handle, name)
        target = self._obj
        # dict 优先按键取
        if isinstance(target, dict) and name in target:
            return _from_python(target[name])
        attr = getattr(target, name, None)
        if attr is None and not hasattr(target, name):
            return None
        return _from_python(attr)

    def bind_call(self, args: List[Any]) -> Any:
        """以 args 调用目标，返回转换后的结果。"""
        if self._is_remote:
            return self._bridge.call_handle(self._handle, args)
        py_args = [_to_python(a) for a in args]
        result = self._obj(*py_args)
        return _from_python(result)

    def bind_getitem(self, idx: Any) -> Any:
        """按索引/键取值。"""
        if self._is_remote:
            return self._bridge.get_handle_attr(self._handle, str(idx))
        try:
            return _from_python(self._obj[_to_python(idx)])
        except (KeyError, IndexError, TypeError) as e:
            return None  # 由调用方报错


class PythonInterop:
    """`use python: { ... }` 的实现：导入真实模块并绑定。"""

    def __init__(self):
        self._bound: Dict[str, InteropValue] = {}

    def bind(self, names: List[str]) -> List[str]:
        """导入并绑定 names 中的每个模块/名称，返回成功绑定的名称。"""
        bound: List[str] = []
        import importlib
        for n in names:
            try:
                mod = importlib.import_module(n)
            except Exception as e:
                raise InteropError(f"use python: 无法导入模块 {n!r}: {e}")
            self._bound[n] = InteropValue(mod, _label=f"python.{n}")
            bound.append(n)
        return bound

    def resolve(self, name: str) -> Optional[InteropValue]:
        return self._bound.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._bound


class InteropBus:
    """总总线：按宿主语言分发。支持 python（直接桥）与 node（JSON-RPC 子进程）。"""

    def __init__(self):
        self.python = PythonInterop()
        self.node: Optional[Any] = None   # 懒加载 NodeBridge
        self._node_bound: Dict[str, InteropValue] = {}

    def _ensure_node(self) -> Any:
        if self.node is None:
            from .node_bridge import NodeBridge
            self.node = NodeBridge()
        return self.node

    def bind(self, lang: str, names: List[str]) -> List[str]:
        if lang == "python":
            self.python.bind(names)
            return names
        if lang == "node":
            bridge = self._ensure_node()
            for n in names:
                self._node_bound[n] = bridge.load_module(n)
            return names
        raise InteropError(f"未知宿主语言 {lang!r}")

    def resolve_python(self, name: str) -> Optional[InteropValue]:
        return self.python.resolve(name)

    def resolve_node(self, name: str) -> Optional[InteropValue]:
        return self._node_bound.get(name)