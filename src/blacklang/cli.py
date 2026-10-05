# -*- coding: utf-8 -*-
"""BlackLang CLI 入口：python -m blacklang <file.bl> 或 -i 交互模式。"""
from __future__ import annotations

import json
import os
import sys

from .checker import static_check
from .interop import InteropBus
from .parser import parse, ParseError
from .scaffold import scaffold
from .tokenizer import LexError
from .typechecker import type_check


def _make_interop():
    return InteropBus()


def _render_error(e: BaseException) -> str:
    return f"[BlackLang 错误] {type(e).__name__}: {e}"


def _load(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def static_check_file(path: str, as_json: bool = False) -> int:
    """只做编译期检查（能力 + 类型），不执行代码。`as_json` 输出机器可读诊断。"""
    if as_json:
        from .diagnostics import collect_from_file, has_errors, render
        try:
            diags = collect_from_file(path)
        except OSError as e:
            print(_render_error(e))
            return 1
        print(render(diags, as_json=True))
        return 1 if has_errors(diags) else 0
    try:
        source = _load(path)
        program = parse(source)
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
    except (LexError, ParseError) as e:
        print(_render_error(e))
        return 1
    all_errors = cap_errors + type_errors
    if all_errors:
        print("[BlackLang 编译期检查失败]")
        from .diagnostics import classify
        for e in all_errors:
            d = classify(e)
            print(f"  ✗ [{d['code']}] {d['message']}")
            if d.get("hint"):
                print(f"      ↳ 建议: {d['hint']}")
        # 能力清单仍给出一部分参考
        m = checker.manifest
        print(f"  程序能力占用意图 : {m.program_caps or '(无敏感操作)'}")
        return 1
    # 输出能力清单
    m = checker.manifest
    print("[BlackLang 编译期检查通过]")
    print(f"  能力清单 program_caps : {m.program_caps or '(无敏感操作)'}")
    print(f"  函数所需能力 per_fn    : {m.per_fn or '(无)'}")
    return 0


def emit_c_file(path: str) -> int:
    """`blacklang --emit-c <f>`：打印 AOT→C 后端生成的 C 源码（不编译）。"""
    from .cbackend import CBackendError, compile_to_c
    try:
        source = _load(path)
        c_src = compile_to_c(parse(source))
    except OSError as e:
        print(_render_error(e))
        return 1
    except (LexError, ParseError) as e:
        print(_render_error(e))
        return 1
    except CBackendError as e:
        from .diagnostics import classify, render
        print(render([classify(str(e))]))
        return 1
    sys.stdout.write(c_src)
    return 0


def native_run_file(path: str) -> int:
    """`blacklang --native <f>`：AOT→C 编译成本机码并运行；不支持则回退 VM。"""
    from .cbackend import CBackendError, build_and_run, c_available, compile_to_c
    try:
        source = _load(path)
        program = parse(source)
    except OSError as e:
        print(_render_error(e))
        return 1
    except (LexError, ParseError) as e:
        print(_render_error(e))
        return 1
    # 编译期(能力+类型)不过不执行（安全默认）
    try:
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
    except Exception as e:  # noqa: BLE001
        print(_render_error(e))
        return 1
    if cap_errors + type_errors:
        print("[BlackLang 编译期拦截，未执行]")
        for e in cap_errors + type_errors:
            print("  ✗ " + e)
        return 1
    if c_available() is None:
        print("[BlackLang native] 未找到 C 编译器，回退到字节码 VM。")
        return run_file(path)
    try:
        c_src = compile_to_c(program)
    except CBackendError as e:
        print(f"[BlackLang native] {e}")
        print("[BlackLang native] 已回退到字节码 VM 路径。")
        return run_file(path)
    result = build_and_run(c_src)
    if not result["ok"]:
        for e in result["errors"]:
            print(_render_error(RuntimeError(e)))
        if result.get("cc_out"):
            sys.stderr.write(result["cc_out"])
        return 1
    sys.stdout.write(result["stdout"])
    return 0


def run_file(path: str) -> int:
    try:
        source = _load(path)
    except OSError as e:
        print(_render_error(e))
        return 1
    try:
        program = parse(source)
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
        errors = cap_errors + type_errors
    except (LexError, ParseError) as e:
        print(_render_error(e))
        return 1
    # 安全默认：编译期(能力+类型)不通过就不运行
    if errors:
        print("[BlackLang 编译期拦截，未执行]")
        for e in errors:
            print("  ✗ " + e)
        return 1
    try:
        # 性能路径：字节码 VM（编译期已通过，无需运行期重复强制 sandbox）
        from .vm import run_vm
        interop = _make_interop()
        run_vm(program, interop=interop)
        return 0
    except Exception as e:
        print(_render_error(e))
        return 1


def sandbox_run_file(path: str, timeout: float = 5.0) -> int:
    """`blacklang --sandbox <file> [--timeout N]`：子进程隔离 + 超时墙运行。"""
    try:
        source = _load(path)
    except OSError as e:
        print(_render_error(e))
        return 1
    from .sandbox import run_in_sandbox
    result = run_in_sandbox(source, timeout=timeout)
    if result.get("timed_out"):
        print(f"[BlackLang 沙箱] ⏱ 执行超时(>{timeout:g}s)，已强制终止子进程（沙箱隔离未外溢）")
        return 1
    if result["ok"]:
        sys.stdout.write(result["stdout"])
        return 0
    print("[BlackLang 沙箱] ❌ 子进程内编译期/运行期拦截：")
    for e in result["errors"]:
        print("  ✗ " + e)
    return 1


def emit_llvm_file(path: str) -> int:
    """`blacklang --emit-llvm <f>`：打印 LLVM IR（AOT 用 opaque 指针）。"""
    from .llvmbackend import compile_to_llvm_ir
    from .cbackend import CBackendError
    try:
        program = parse(_load(path))
        ir = compile_to_llvm_ir(program)
    except OSError as e:
        print(_render_error(e))
        return 1
    except (LexError, ParseError) as e:
        print(_render_error(e))
        return 1
    except CBackendError as e:
        from .diagnostics import classify, render
        print(render([classify(str(e))]))
        return 1
    sys.stdout.write(ir)
    return 0


def llvm_run_file(path: str, use_jit: bool = False) -> int:
    """`blacklang --llvm/--jit <f>`：LLVM 编译（AOT 或进程内 JIT）并运行。"""
    from .llvmbackend import jit_available, llvm_available, run_llvm
    from .cbackend import CBackendError
    try:
        program = parse(_load(path))
    except OSError as e:
        print(_render_error(e))
        return 1
    except (LexError, ParseError) as e:
        print(_render_error(e))
        return 1
    # 编译期(能力+类型)不过不执行（安全默认）
    try:
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
    except Exception as e:  # noqa: BLE001
        print(_render_error(e))
        return 1
    if cap_errors + type_errors:
        print("[BlackLang 编译期拦截，未执行]")
        for e in cap_errors + type_errors:
            print("  ✗ " + e)
        return 1
    if use_jit and not jit_available():
        print("[BlackLang jit] 未找到可用的 llvmlite，回退到 LLVM AOT(clang) 路径。")
        use_jit = False
    if not use_jit and llvm_available() is None:
        print("[BlackLang llvm] 未找到 clang，回退到字节码 VM。")
        return run_file(path)
    try:
        res = run_llvm(program, use_jit=use_jit)
    except CBackendError as e:
        print(f"[BlackLang llvm] {e}")
        print("[BlackLang llvm] 已回退到字节码 VM 路径。")
        return run_file(path)
    if not res["ok"]:
        for e in res["errors"]:
            print(_render_error(RuntimeError(e)) if e else "[BlackLang llvm] 运行失败")
        if res.get("cc_out"):
            sys.stderr.write(res["cc_out"])
        return 1
    sys.stdout.write(res["stdout"])
    return 0


def _looks_complete(src: str) -> bool:
    """是否已是一段完整代码：当花括号括号配平(深度=0)即视为可提交执行。"""
    depth = src.count("{") - src.count("}")
    return depth <= 0


def repl() -> None:
    from . import __version__
    from .evaluator import Evaluator
    print(f"BlackLang REPL {__version__} —— 输入代码即时执行；exit 或 Ctrl-D 退出")
    # 复用同一个会话，跨行保留变量与函数定义
    session = Evaluator(parse(""))
    buf: list = []
    while True:
        prompt = "..> " if buf else "bl> "
        try:
            line = input(prompt)
        except EOFError:
            break
        if not buf:
            stripped = line.strip()
            if stripped in ("exit", "quit", ":q"):
                break
            if not stripped:
                continue
        buf.append(line)
        src = "\n".join(buf)
        if not _looks_complete(src):
            continue                      # 继续多行输入到花括号配平
        buf = []
        if not src.strip():
            continue
        try:
            session.program = parse(src)
            session.run()
        except Exception as e:
            print(_render_error(e))


def manifest_file(path: str) -> int:
    """输出程序的能力清单（JSON）——给 AI/工具链做「运行前自证 + 审计」。"""
    try:
        source = _load(path)
        program = parse(source)
        checker, cap_errors = static_check(program)
        type_errors = type_check(program, checker)
        m = checker.manifest
    except (LexError, ParseError) as e:
        print(_render_error(e))
        return 1
    result = {
        "file": path,
        "capabilities": m.program_caps,
        "per_function": m.per_fn,
        "unauthorized_calls": m.unauthorized,
        "compile_errors": cap_errors + type_errors,
        "verdict": "pass" if not (cap_errors or type_errors) else "fail",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verdict"] == "pass" else 1


def init_project(target: str) -> int:
    """`blacklang --init <dir>` 生成最小可运行项目。"""
    name = os.path.basename(os.path.abspath(target)) or "BlackLang Project"
    created = scaffold(target, name)
    print(f"创建 BlackLang 项目于: {target}")
    for c in created:
        print("  + " + c)
    print("下一步: blacklang {}/src/main.bl".format(target.rstrip("/")))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        repl()
        return 0
    if args[0] in ("-i", "--repl"):
        repl()
        return 0
    if args[0] in ("--check", "-c"):
        if len(args) < 2:
            print("用法: python -m blacklang --check <file.bl> [--json]")
            return 1
        return static_check_file(args[1], as_json=("--json" in args))
    if args[0] == "--emit-llvm":
        if len(args) < 2:
            print("用法: python -m blacklang --emit-llvm <file.bl>")
            return 1
        return emit_llvm_file(args[1])
    if args[0] == "--llvm":
        if len(args) < 2:
            print("用法: python -m blacklang --llvm <file.bl>")
            return 1
        return llvm_run_file(args[1], use_jit=False)
    if args[0] == "--jit":
        if len(args) < 2:
            print("用法: python -m blacklang --jit <file.bl>")
            return 1
        return llvm_run_file(args[1], use_jit=True)
    if args[0] == "--emit-c":
        if len(args) < 2:
            print("用法: python -m blacklang --emit-c <file.bl>")
            return 1
        return emit_c_file(args[1])
    if args[0] in ("--native", "-n"):
        if len(args) < 2:
            print("用法: python -m blacklang --native <file.bl>")
            return 1
        return native_run_file(args[1])
    if args[0] in ("--manifest", "-m"):
        if len(args) < 2:
            print("用法: python -m blacklang --manifest <file.bl>")
            return 1
        return manifest_file(args[1])
    if args[0] in ("--init", "--new"):
        if len(args) < 2:
            print("用法: python -m blacklang --init <dir>")
            return 1
        return init_project(args[1])
    if args[0] in ("--sandbox", "-s"):
        # 支持 `--sandbox <file> [--timeout N]`
        if len(args) < 2:
            print("用法: python -m blacklang --sandbox <file.bl> [--timeout 秒]")
            return 1
        timeout = 5.0
        i = 2
        while i < len(args):
            if args[i] == "--timeout" and i + 1 < len(args):
                try:
                    timeout = float(args[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
        return sandbox_run_file(args[1], timeout=timeout)
    if args[0] in ("-h", "--help"):
        print("BlackLang CLI")
        print("  python -m blacklang <file.bl>     运行程序(VM 性能路径)")
        print("  python -m blacklang --native <f>  AOT→C 编译成本机码并运行(不支持则回退 VM)")
        print("  python -m blacklang --emit-c <f>  打印 AOT→C 生成的 C 源码")
        print("  python -m blacklang --llvm <f>    AOT→LLVM IR → clang 编译本机码并运行")
        print("  python -m blacklang --emit-llvm <f> 打印 LLVM IR")
        print("  python -m blacklang --jit <f>     llvmlite 进程内 JIT 运行(需 llvmlite)")
        print("  python -m blacklang --check <f>   编译期检查(能力+类型), 加 --json 输出结构化诊断")
        print("  python -m blacklang --manifest <f> 输出能力清单 JSON")
        print("  python -m blacklang --sandbox <f> 子进程沙箱运行(超时墙), 加 --timeout N")
        print("  python -m blacklang --init <dir>  生成最小可运行项目")
        print("  python -m blacklang -i             REPL 交互")
        return 0
    return run_file(args[0])


if __name__ == "__main__":
    sys.exit(main())