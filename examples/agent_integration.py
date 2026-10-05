# -*- coding: utf-8 -*-
"""AI 代理（LangChain / DeepSeek / 任意 LLM 工具链）如何使用 BlackLang。

核心可复用的「工具函数」：给 AI 提供 blacklang_exec / blacklang_check 两个工具——
先「编译期自证安全」，安全了才执行。这正契合 BlackLang「让 AI 快速、安全地
做到任何项目」。

此文件是可独立运行的无依赖演示。若项目装有 langchain，可用 `@tool` 包装成
LangChain 工具（见文件末尾注释）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from blacklang.api import compile_check, run   # noqa: E402


# ---- 1. 给 LLM 的两个工具函数 ----
def blacklang_check(code: str) -> str:
    """编译期检查一段 BlackLang 代码，返回 JSON。安全默认：检查不通过就不该运行。"""
    return json.dumps(compile_check(code), ensure_ascii=False)


def blacklang_exec(code: str) -> str:
    """运行一段 BlackLang 代码。先静态检查，不安全则拒绝执行并说明原因。"""
    return json.dumps(run(code), ensure_ascii=False)


# ---- 2. 模拟「AI 想先查一下再跑」的调用流程 ----
def demo():
    print("=" * 72)
    print("AI 代理工作流演示：先检查 -> 通过则执行 -> 失败则不给执行")
    print("=" * 72)

    # (a) AI 想写一个需要读文件+写小程序
    safe_code = '''
run main: {
    let data = [1, 2, 3, 4, 5];
    let s = 0;
    for v in data: { s = s + v; }
    print("总和 =", s);
    sandbox readonly-fs: {
        print("可读长度 =", len(read("config.json")));
    }
}
'''

    print("\n[1] AI 生成一段自认为安全的代码")
    verdict = compile_check(safe_code)
    print("    编译期检查:", "✅ 通过" if verdict["ok"] else "❌ 失败", verdict["capabilities"])

    print("\n[2] 检查通过 → 交给执行工具")
    result = run(safe_code)
    print("    运行结果:", repr(result["stdout"]), "| ok =", result["ok"])

    # (b) AI 想访问网络，但放在了错误权限域
    unsafe_code = '''
run main: {
    sandbox readonly-fs: {
        let r = http_get("https://example.com");
        print(r);
    }
}
'''
    print("\n[3] AI 又想访问网络（但权限域写错了）")
    verdict = compile_check(unsafe_code)
    print("    编译期检查:", "❌ 拒绝 ->", verdict["errors"])

    print("\n[4] 检查失败 → 执行工具也会拒绝运行（双保险）")
    result = run(unsafe_code)
    print("    执行工具返回 ok =", result["ok"], "| errors =", result["errors"])

    print("\n" + "=" * 72)
    print("结论：即使 AI 写错权限，BlackLang 也在运行前就拦住，不会酿成后果。")


# 若安装了 langchain，可这样把它包装成 LangChain 的 @tool：
#
#   from langchain.tools import tool
#   @tool
#   def blacklang_tool(code: str) -> str:
#       """执行可选的 BlackLang 脚本（先静态安全检查）。"""
#       return json.dumps(run(code))

if __name__ == "__main__":
    demo()