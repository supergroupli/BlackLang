# -*- coding: utf-8 -*-
"""BlackLang 结构化诊断（AI/工具链可机读）。

把编译期(词法/语法/能力/类型)与运行期错误统一成**机器可读诊断**：

    {"severity": "error", "code": "BL1001", "line": 3, "col": 12,
     "message": "编译期越权拒绝: http_get 需要权限 'net' …",
     "hint": "把该调用放进 `sandbox net: { … }` 内，或去掉该调用",
     "stage": "capability"}

LLM/代理拿到行号 + 错误码 + 修复提示即可**定位并自纠**，无需解析人类文案。
`blacklang --check f.bl --json` 与 `api.diagnostics()` 都走这里。

错误码表：
  BL0001 词法/语法错误        BL1001 能力越权(编译期拒绝)
  BL1002 未定义名称           BL1003 类型不匹配
  BL1004 函数返回类型不符      BL2001 C 后端不支持的构造
  BL9001 运行期错误           BL9002 运行超时(沙箱)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

SEV_ERROR = "error"
SEV_WARNING = "warning"

# 诊断阶段
ST_LEXPARSE = "parse"
ST_CAPABILITY = "capability"
ST_TYPE = "type"
ST_RUNTIME = "runtime"
ST_CBACKEND = "cbackend"

_HINT = {
    "BL1001": "把该调用放进授予对应权限的 `sandbox <权限>: { … }` 内；或去掉该调用",
    "BL1002": "检查变量/函数名拼写；用 `let <名字> = …` 先声明，或改用已定义的名字",
    "BL1003": "统一两侧类型（如 `str(x)` 转字符串），或去掉类型标注让推断接管",
    "BL1004": "让 return 的类型与 `-> 类型` 声明一致，或去掉返回类型标注",
    "BL0001": "检查该行附近的语法（花括号配对、逗号、分号、括号）",
    "BL2001": "该构造 C 后端暂不支持；用 VM 或 AOT→Python 路径运行",
    "BL9001": "查看 message：多为索引/键越界或类型不符；加边界检查",
    "BL9002": "程序耗时过长或死循环；用 `--timeout` 调大，或优化/修正循环",
}

_LINE_COL = re.compile(r"(\d+):(\d+)")


def _mk(code: str, stage: str, message: str, line: Optional[int] = None,
        col: Optional[int] = None, severity: str = SEV_ERROR,
        hint: Optional[str] = None) -> Dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "line": line,
        "col": col,
        "message": message,
        "hint": hint if hint is not None else _HINT.get(code, ""),
        "stage": stage,
    }


def classify(message: str) -> Dict[str, Any]:
    """把一条已有的错误字符串分类为结构化诊断。"""
    msg = message.strip()
    # 词法/语法：形如 "3:12 期望 ';'，但得到 '}'"
    m = _LINE_COL.match(msg)
    if m:
        return _mk("BL0001", ST_LEXPARSE, msg, int(m.group(1)), int(m.group(2)))
    if "越权" in msg:
        # 抽取行号（若有）
        lm = re.search(r"(\d+):(\d+)", msg)
        line = int(lm.group(1)) if lm else None
        col = int(lm.group(2)) if lm else None
        return _mk("BL1001", ST_CAPABILITY, msg, line, col)
    if "未定义的名称" in msg:
        return _mk("BL1002", ST_TYPE, msg)
    if "返回类型" in msg:
        return _mk("BL1004", ST_TYPE, msg)
    if "类型" in msg:
        return _mk("BL1003", ST_TYPE, msg)
    if "C 后端" in msg:
        return _mk("BL2001", ST_CBACKEND, msg)
    if "超时" in msg or "timed out" in msg:
        return _mk("BL9002", ST_RUNTIME, msg)
    return _mk("BL9001", ST_RUNTIME, msg)


def collect(source: str) -> List[Dict[str, Any]]:
    """对一份源码做编译期检查并返回结构化诊断（不执行）。"""
    from .checker import static_check
    from .parser import parse, ParseError
    from .tokenizer import LexError
    from .typechecker import type_check

    try:
        program = parse(source)
    except (LexError, ParseError) as e:
        return [classify(str(e))]
    try:
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
    except Exception as e:  # noqa: BLE001
        return [classify(f"{type(e).__name__}: {e}")]
    return [classify(m) for m in (cap_errors + type_errors)]


def collect_from_file(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return collect(f.read())


def has_errors(diags: List[Dict[str, Any]]) -> bool:
    return any(d["severity"] == SEV_ERROR for d in diags)


def render(diags: List[Dict[str, Any]], as_json: bool = False) -> str:
    """人类可读文本或 JSON。"""
    if as_json:
        import json
        return json.dumps({"ok": not has_errors(diags), "diagnostics": diags},
                          ensure_ascii=False, indent=2)
    if not diags:
        return "[BlackLang] 编译期检查通过，无诊断。"
    lines = []
    for d in diags:
        loc = ""
        if d.get("line") is not None:
            loc = f" ({d['line']}:{d.get('col') or 0})"
        lines.append(f"  ✗ [{d['code']}]{loc} {d['message']}")
        if d.get("hint"):
            lines.append(f"      ↳ 建议: {d['hint']}")
    return "\n".join(lines)
