# -*- coding: utf-8 -*-
"""BlackLang —— 面向 AI 的编程语言。

让 AI 快速、安全地做到任何想做的项目。核心能力：
- 编译期静态检查（能力分析 + 类型检查），把越权/类型错误「提前」到运行前暴露；
- Interop Bus：`use python: {...}` / `use node: {...}` 打通主流宿主语言生态；
- 面向 AI 的 Python API：compile_check / manifest / run（安全默认，检查通过才运行）。
"""
from __future__ import annotations

__version__ = "0.2.0"

from .api import compile_check, diagnose as diagnose, manifest as manifest, \
    native_run as native_run, run as run                                       # noqa: F401
from .checker import CapabilityManifest, StaticChecker, static_check      # noqa: F401
from .evaluator import run_program
from .interop import InteropBus, InteropValue                             # noqa: F401
from .parser import parse
from .tokenizer import tokenize
from .typechecker import type_check                                              # noqa: F401
from .vm import run_vm, Compiler, VM                                              # noqa: F401

__all__ = [
    "run_program", "parse", "tokenize",
    "static_check", "StaticChecker", "CapabilityManifest",
    "type_check", "compile_check", "manifest", "run",
    "native_run", "diagnose",
    "InteropBus", "InteropValue",
    "run_vm", "Compiler", "VM", "__version__",
]