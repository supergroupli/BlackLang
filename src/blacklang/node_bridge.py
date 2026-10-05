# -*- coding: utf-8 -*-
"""Interop Bus · Node 宿主桥（通过 JSON-RPC + 子进程）。

BlackLang 的解释器当前运行在 Python 内，所以调用 Node 生态需要把请求发到
一个常驻的 Node 子进程。该桥通过 stdio JSON-RPC 与 Node 侧通信：

- 对象用不透明 handle id 表示，实际对象存在 Node 进程内；
- 属性访问 / 调用 / 取值通过 RPC(id, op, args...) 完成；
- 返回 JSON 可序列化值时直接返回；否则返回新的 handle id，包装成 InteropValue。

安全提示：向 Node 宿主开放互操作=可执行任意 JS；能力清单会标记 use node，审计友好。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional

from .interop import InteropValue

_RUNNER = os.path.join(os.path.dirname(__file__), "node_runner.mjs")

# Node 侧返回的 handle 包装：{"$handle": id}
_HANDLE_KEY = "$handle"


class NodeBridge:
    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._next_id = 1
        self._lock = threading.Lock()

    def _ensure_proc(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        import shutil
        node_bin = os.environ.get("BLACKLANG_NODE", "") or shutil.which("node") or shutil.which("nodejs")
        if not node_bin:
            # 回退到常见捆绑路径
            for p in (
                "/Users/superlee/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node",
                "/usr/local/bin/node",
            ):
                if os.path.exists(p):
                    node_bin = p
                    break
        if not node_bin:
            raise RuntimeError("未找到 node 可执行文件（可设置 BLACKLANG_NODE 指定）")
        self._proc = subprocess.Popen(
            [node_bin, _RUNNER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        # 分线程读 stderr 避免阻塞
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            for line in self._proc.stderr:
                pass
        except Exception:
            pass

    def _rpc(self, method: str, params: List[Any]) -> Any:
        self._ensure_proc()
        req = {"id": self._next_id, "method": method, "params": self._sanitize(params)}
        self._next_id += 1
        with self._lock:
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("Node 宿主进程退出或没有响应")
        resp = json.loads(line)
        if resp.get("error"):
            raise RuntimeError(f"Node 错误: {resp['error']}")
        return resp.get("result")

    def _sanitize(self, val: Any) -> Any:
        """把 Python 值/InteropValue 编码成可发往 Node 的数据。"""
        if isinstance(val, InteropValue):
            if getattr(val, "_is_remote", False):
                return {"$handle": val._handle}
            return str(val)
        if isinstance(val, dict):
            return {k: self._sanitize(v) for k, v in val.items()}
        if isinstance(val, (list, tuple)):
            return [self._sanitize(v) for v in val]
        if isinstance(val, (str, int, float, bool)) or val is None:
            return val
        return str(val)

    def _to_python_value(self, val: Any) -> Any:
        """把 Node 返回的数据解码回 BlackLang 值（handle -> InteropValue）。"""
        if isinstance(val, dict) and _HANDLE_KEY in val:
            return InteropValue(_label=f"node#{val[_HANDLE_KEY]}",
                                _bridge=self, _handle=val[_HANDLE_KEY])
        if isinstance(val, dict):
            return {k: self._to_python_value(v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._to_python_value(v) for v in val]
        return val

    # ---- 供 InteropValue 使用的透传方法 ----
    def call_handle(self, handle: int, args: List[Any]) -> Any:
        res = self._rpc("call", [handle, [self._sanitize(a) for a in args]])
        return self._to_python_value(res)

    def get_handle_attr(self, handle: int, name: str) -> Any:
        res = self._rpc("get", [handle, name])
        return self._to_python_value(res)

    def load_module(self, name: str) -> InteropValue:
        res = self._rpc("load", [name])
        return InteropValue(_label=f"node.{name}", _bridge=self, _handle=res[_HANDLE_KEY])

    def close(self):
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None