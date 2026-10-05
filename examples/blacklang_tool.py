# -*- coding: utf-8 -*-
"""BlackLang × LangChain 工具适配器（blacklang_tool）。

把 BlackLang 封装成一个可被 LLM 直接调用的 Tool：
  - 编译期(能力+类型)自检 —— 不过就不执行（安全默认）；
  - 可选跨进程沙箱 + 超时墙（`use_sandbox=True`）；
  - 输出固定的简单结果字典，AI 无需懂 BlackLang 内部。

若你装有 LangChain / LangGraph，直接：
    from examples.blacklang_tool import blacklang_tool
    from langchain_core.tools import Tool
    tool = Tool.from_function(blacklang_tool)

未装 LangChain 时，本模块提供 `blacklang_tool({...}) -> dict` 的纯函数形态，
可直接被你自己写的 Agent 循环 / DeepSeek function-calling 调用 —— 零依赖可用。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

# 指向项目 src（便于直接用解释器跑示例）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

NAME = "blacklang_tool"
DESCRIPTION = (
    "执行一段 BlackLang 程序。入参 code: String = BlackLang 源码；"
    "use_sandbox: bool = 是否跨进程沙箱隔离（防死循环/崩溃外溢，默认 True）。"
    "执行前自动做编译期(能力+类型)自检，不安全则不运行。返回 stdout 或错误。"
)
ARGS_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "BlackLang 程序源码"},
        "use_sandbox": {"type": "boolean", "description": "是否沙箱运行", "default": True},
    },
    "required": ["code"],
}


def blacklang_tool(props: Dict[str, Any]) -> Dict[str, Any]:
    """@tool 的实现体：编译自检 + 运行（默认走沙箱）→ 返回简单结果字典。"""
    code = str(props.get("code", ""))
    use_sandbox = bool(props.get("use_sandbox", True))

    # 编译期自检：能力 + 类型，不过就不运行（安全默认）
    from blacklang.checker import static_check
    from blacklang.parser import parse, ParseError
    from blacklang.typechecker import type_check
    from blacklang.tokenizer import LexError
    try:
        program = parse(code)
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
        errors = cap_errors + type_errors
    except (LexError, ParseError) as e:
        return {"ok": False, "stdout": "", "errors": [f"{type(e).__name__}: {e}"]}
    if errors:
        return {"ok": False, "stdout": "", "errors": errors, "intercepted": True}

    # 运行：沙箱(默认) 或 解释路径
    if use_sandbox:
        from blacklang.sandbox import run_in_sandbox
        res = run_in_sandbox(code, timeout=5.0)
        return {
            "ok": res["ok"],
            "stdout": res.get("stdout", ""),
            "errors": res.get("errors", []),
            "timed_out": res.get("timed_out", False),
        }
    import io
    import contextlib
    from blacklang.vm import run_vm
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            run_vm(program)
        return {"ok": True, "stdout": out.getvalue(), "errors": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stdout": out.getvalue(), "errors": [f"{type(e).__name__}: {e}"]}


# ------------------ LangChain 适配（按需启用，未安装也不报错） ------------------
def to_langchain_tool():
    """返回可用作 LangChain Tool 的对象；未装 LangChain 时返回 None。"""
    try:
        from langchain_core.tools import Tool  # noqa: F401
    except ImportError:
        return None
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "args_schema": ARGS_SCHEMA,
        "func": blacklang_tool,
    }
    # 若你的框架用 `@tool` 装饰器，等价写法：
    #   from langchain_core.tools import tool
    #   @tool; def blacklang_tool(code: str, use_sandbox: bool = True) -> dict:
    #       return tool_impl({"code": code, "use_sandbox": use_sandbox})


if __name__ == "__main__":
    # 自演示：模拟一个 LLM 调用该工具
    demo = "run main: { let s=0; for i in [1,2,3,4,5]: { s=s+i; } print(s); }"
    print("-- 正常执行 --")
    print(blacklang_tool({"code": demo}))
    bad = 'run main: { let x = http_get("http://x"); print(x); }'
    print("-- 越权(编译期拦截,未执行) --")
    print(blacklang_tool({"code": bad}))