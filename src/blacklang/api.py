# -*- coding: utf-8 -*-
"""BlackLang 面向 AI / 工具链的 Python API。

让 LangChain、DeepSeek、CI 等外部编排层能「编译期先验，运行期后验」：
    check(source)  -> {ok, capabilities, ...}   // 运行前自证
    run(source)    -> stdout 捕获结果
    manifest(source)-> 能力清单 JSON 化 dict
"""
from __future__ import annotations

import contextlib
import io
from typing import Any, Dict

from .checker import static_check
from .evaluator import Evaluator, run_program
from .parser import parse, ParseError
from .tokenizer import LexError
from .typechecker import type_check


def _compile(source: str):
    """解析并做完整静态检查。返回 (program, checker, errors)。"""
    program = parse(source)
    checker, cap_errors = static_check(program)
    type_errors = type_check(program, checker)
    return program, checker, cap_errors + type_errors


def compile_check(source: str) -> Dict[str, Any]:
    """只做编译期检查，不运行。返回 {ok, capabilities, per_function, unauthorized, errors}。"""
    try:
        program, checker, errors = _compile(source)
    except (LexError, ParseError) as e:
        return {"ok": False, "errors": [str(e)], "capabilities": [],
                "per_function": {}, "unauthorized": []}
    m = checker.manifest
    return {
        "ok": not errors,
        "capabilities": m.program_caps,
        "per_function": m.per_fn,
        "unauthorized": m.unauthorized,
        "errors": errors,
    }


def manifest(source: str) -> Dict[str, Any]:
    """返回程序能力清单（dict），供审计/自证。"""
    try:
        program, checker, errors = _compile(source)
    except (LexError, ParseError) as e:
        return {"capabilities": [], "errors": [str(e)], "verdict": "fail"}
    m = checker.manifest
    return {
        "capabilities": m.program_caps,
        "per_function": m.per_fn,
        "unauthorized": m.unauthorized,
        "errors": errors,
        "verdict": "pass" if not errors else "fail",
    }


def run(source: str) -> Dict[str, Any]:
    """先静态检查，若失败则不执行（安全默认）；通过则运行并返回 stdout。
    性能路径：字节码 VM（编译期已通过，运行期无需重复强制 sandbox）。"""
    try:
        program, checker, errors = _compile(source)
    except (LexError, ParseError) as e:
        return {"ok": False, "stdout": "", "errors": [str(e)]}
    if errors:
        return {"ok": False, "stdout": "", "errors": errors}
    out = io.StringIO()
    try:
        from .vm import run_vm
        with contextlib.redirect_stdout(out):
            run_vm(program)
        return {"ok": True, "stdout": out.getvalue(), "errors": []}
    except Exception as e:
        return {"ok": False, "stdout": out.getvalue(), "errors": [str(e)]}


def native_run(source: str) -> Dict[str, Any]:
    """AOT→C 编译成本机码并运行（性能第三阶）。
    返回 {ok, stdout, errors, c_source, fallback}；C 后端不支持时 fallback=True 走 VM。"""
    try:
        program, checker, errors = _compile(source)
    except (LexError, ParseError) as e:
        return {"ok": False, "stdout": "", "errors": [str(e)], "fallback": False}
    if errors:
        return {"ok": False, "stdout": "", "errors": errors, "fallback": False}
    from .cbackend import CBackendError, build_and_run, c_available, compile_to_c
    if c_available() is None:
        res = run(source)
        res["fallback"] = True
        return res
    try:
        c_src = compile_to_c(program)
    except CBackendError as e:
        res = run(source)
        res["fallback"] = True
        res.setdefault("errors", [])
        res["note"] = str(e)
        return res
    out = build_and_run(c_src)
    out["fallback"] = False
    return out


def llvm_run(source: str, use_jit: bool = False) -> Dict[str, Any]:
    """走 LLVM 路径运行：AOT(clang 编译 IR) 或进程内 JIT(llvmlite)。
    返回 {ok, stdout, errors, ir, jit, ptr_mode, fallback}。"""
    try:
        program, checker, errors = _compile(source)
    except (LexError, ParseError) as e:
        return {"ok": False, "stdout": "", "errors": [str(e)], "fallback": False}
    if errors:
        return {"ok": False, "stdout": "", "errors": errors, "fallback": False}
    from .llvmbackend import jit_available, llvm_available, run_llvm
    if use_jit and not jit_available():
        res = run_llvm(program, use_jit=False)
        res["fallback"] = True
        return res
    if llvm_available() is None:
        res = run(source)
        res["fallback"] = True
        return res
    res = run_llvm(program, use_jit=use_jit)
    res["fallback"] = False
    return res


def diagnose(source: str) -> Dict[str, Any]:
    """结构化诊断（机器可读），供 LLM/代理定位并自纠。
    注意：函数名为 `diagnose`，避免与 `blacklang.diagnostics` 子模块同名。"""
    from .diagnostics import collect, has_errors
    diags = collect(source)
    return {"ok": not has_errors(diags), "diagnostics": diags}