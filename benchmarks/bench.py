#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlackLang 性能基准（可复现基线，三条执行路径）。

  1. 解释路径：字节码 VM（run_vm）           —— 日常开发/AI 直觉速度
  2. 编译路径：AOT→Python（codegen）          —— 算法繁重任务，逼近 CPython
  3. 参照线：  CPython 原生                    —— 「介于 Python 与 C 之间」的判据之一

比率 = BlackLang 时间 / CPython 时间；比率越接近/低于 1 越接近原生速度。

用法：
  python3 benchmarks/bench.py            # 全部
  python3 benchmarks/bench.py fib        # 只跑 fib
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from blacklang.parser import parse                              # noqa: E402
from blacklang.vm import run_vm                                 # noqa: E402
from blacklang.codegen import compile_to_python                 # noqa: E402
from blacklang.cbackend import compile_to_c, c_available        # noqa: E402

FIB_N = 26
LOOP_N = 2_000_000

# 手写 C 参照（与 BL 程序等价，clang -O2）
C_FIB = f"""
#include <stdio.h>
static long long fib(long long n) {{ return n < 2 ? n : fib(n-1) + fib(n-2); }}
int main(void) {{ printf("%lld\\n", fib({FIB_N})); return 0; }}
"""

C_LOOP = f"""
#include <stdio.h>
int main(void) {{
    long long s = 0, i = 0;
    while (i < {LOOP_N}LL) {{ s += i; i += 1; }}
    printf("%lld\\n", s);
    return 0;
}}
"""

BL_FIB = f"""
fn fib(n: int) -> int: {{
    if n < 2: {{ return n; }}
    return fib(n - 1) + fib(n - 2);
}}
run main: {{ print(fib({FIB_N})); }}
"""

BL_LOOP = f"""
run main: {{
    let s = 0;
    let i = 0;
    while i < {LOOP_N}: {{
        s = s + i;
        i = i + 1;
    }}
    print(s);
}}
"""

# ---------------- runner ----------------
def _capture(fn):
    out = StringIO()
    with redirect_stdout(out):
        fn()
    return out.getvalue().strip()


def run_vm_src(src: str):
    prog = parse(src)
    return _capture(lambda: run_vm(prog))


def run_aot_src(src: str):
    prog = parse(src)
    ns = {}
    exec(compile(compile_to_python(prog), "<bl-aot>", "exec"), ns)
    return _capture(lambda: ns["main"]())


# Python 参照直接给出返回值的函数
def py_fib(n):
    if n < 2:
        return n
    return py_fib(n - 1) + py_fib(n - 2)


def py_loop(n):
    s = 0
    i = 0
    while i < n:
        s += i
        i += 1
    return s


def timeit(fn, repeats=3):
    fn()  # 预热
    best = float("inf")
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


# ---- C 路径：编译一次，只测执行时间 ----
def compile_c(c_src: str) -> str:
    import subprocess
    import tempfile
    cc = c_available()
    d = tempfile.mkdtemp(prefix="bl_bench_")
    c_path = os.path.join(d, "p.c")
    exe = os.path.join(d, "p")
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(c_src)
    subprocess.run([cc, "-O2", "-std=c11", "-o", exe, c_path, "-lm"],
                   capture_output=True, text=True, check=True)
    return exe


def run_exe(exe: str):
    import subprocess
    return subprocess.run([exe], capture_output=True, text=True).stdout


def bench_workload(label, bl_src, py_fn, c_ref_src=None):
    vm_t = timeit(lambda: run_vm_src(bl_src))
    aot_t = timeit(lambda: run_aot_src(bl_src))
    py_t = timeit(py_fn)
    c_t = cref_t = compile_ms = None
    if c_available():
        t = time.perf_counter()
        exe_bl = compile_c(compile_to_c(parse(bl_src)))
        compile_ms = (time.perf_counter() - t) * 1000
        c_t = timeit(lambda: run_exe(exe_bl))
        if c_ref_src:
            exe_ref = compile_c(c_ref_src)          # 只编译一次，不计入计时
            cref_t = timeit(lambda: run_exe(exe_ref))
    print()
    print(f"◆ {label}")
    print(f"   解释(VM)        {vm_t*1000:9.1f} ms   vs CPython {vm_t/py_t:7.2f}x")
    print(f"   编译(AOT→Python) {aot_t*1000:9.1f} ms   vs CPython {aot_t/py_t:7.2f}x")
    if c_t is not None:
        print(f"   编译(AOT→C)      {c_t*1000:9.1f} ms   vs CPython {c_t/py_t:7.2f}x"
              f"   vs 手写C {c_t/cref_t:6.2f}x")
        print(f"   手写 C 参照      {cref_t*1000:9.1f} ms   (纯执行, 一次性 clang -O2 编译 {compile_ms:.0f}ms 未计入)")
    print(f"   参照(CPython)    {py_t*1000:9.1f} ms   (1.00x)")
    return vm_t, aot_t, py_t, c_t


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("=" * 74)
    print("BlackLang 性能基准 · 解释 / AOT→Python / AOT→C / CPython（越小越快）")
    print("=" * 74)
    if which in ("all", "fib"):
        bench_workload(f"fib({FIB_N})  递归密集", BL_FIB, lambda: py_fib(FIB_N), C_FIB)
    if which in ("all", "loop"):
        bench_workload(f"while 循环 ×{LOOP_N}  算术/调度", BL_LOOP,
                       lambda: py_loop(LOOP_N), C_LOOP)
    print()
    print("结论：AOT→C 把 BlackLang 编译成本机码，性能进入「Python 与 C 之间」的 C 侧；")
    print("      AOT→Python ≈ CPython；解释(VM) 适合实时/REPL 与互操作。")
    print("全面数值见 benchmarks/RESULTS.md。")


if __name__ == "__main__":
    main()