# -*- coding: utf-8 -*-
"""BlackLang 跨进程沙箱执行。

把一段 BlackLang 程序放到**独立子进程**里运行，与宿主进程隔离：
- 程序崩溃/抛异常 不拖垮宿主；
- 死循环/超长任务 由父进程在 `timeout` 后强制 `terminate()`；
- 子进程里**依然**先做编译期(能力+类型)检查，不过就不执行（安全默认）；
- 运行期再套 Evaluator 的能力强制（两层沙箱）。

这样 AI 写出的多行脚本既有「崩溃/超时不外溢」的边界墙，又有编译期拦截的硬门槛，
形成 编译期拦截 + 运行期强制 + 跨进程墙 3 层纵深。

父端接口 `run_in_sandbox` 并发安全（每次一个子进程）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _worker(conn, source: str):
    """在子进程内运行 BL 源码，结果经 pipe 回传。"""
    try:
        from .parser import parse
        from .checker import static_check
        from .typechecker import type_check
        from .evaluator import Evaluator
        import io
        import contextlib

        program = parse(source)
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
        errors = cap_errors + type_errors
        out = io.StringIO()
        if errors:
            conn.send({"ok": False, "stdout": "", "errors": errors})
            return
        with contextlib.redirect_stdout(out):
            Evaluator(program).run()
        conn.send({"ok": True, "stdout": out.getvalue(), "errors": []})
    except Exception as e:  # noqa: BLE001  —— 边界墙：任何错误都回传而非崩溃父进程
        try:
            conn.send({"ok": False, "stdout": "", "errors": [f"{type(e).__name__}: {e}"]})
        except Exception:
            pass


def run_in_sandbox(source: str, timeout: float = 5.0, uses_vm: bool = False) -> Dict[str, Any]:
    """在子进程里用超时墙运行 BL 源码。`timeout=None` 表示不限时（不推荐）。"""
    import multiprocessing as mp

    ctx = mp.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=_worker, args=(child, source), daemon=True)
    p.start()
    child.close()
    try:
        if timeout is None:
            ok, result = parent.recv(), None
        else:
            if parent.poll(timeout):
                result = parent.recv()
            else:
                result = None
    finally:
        # 死了无限循环 → 硬杀
        if p.is_alive():
            p.terminate()
            p.join(timeout=2)
            if p.is_alive():
                p.kill()
    if result is None:
        return {"ok": False, "stdout": "", "errors": [f"执行超时（>{timeout:g}s）或子进程无响应"], "timed_out": True}
    result.setdefault("timed_out", False)
    return result