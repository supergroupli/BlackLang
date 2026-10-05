# BlackLang 性能基准（可复现）

> 目标坐标：**介于 Python 与 C 之间**。本页记录五条执行路径的可复现基线。

同一台机器（Apple Silicon / Apple clang 21(LLVM) / CPython 3.12）上
`python3 benchmarks/bench.py` 测得（越小越快）。`比率 = BlackLang 时间 / CPython 时间`。

| 工作负载 | 解释(VM) | AOT→Python | AOT→C | **AOT→LLVM** | 手写 C | CPython |
|----------|----------|------------|-------|--------------|--------|---------|
| `fib(26)` 递归 | ~398 ms (56.6x) | 7.2 ms (1.02x) | 6.6 ms (0.94x) | **3.6 ms (0.52x)** | 4.3 ms | 7.0 ms |
| `while 200万` 算术 | ~1615 ms (35.5x) | 42 ms (0.92x) | 7.2 ms (0.16x) | **6.6 ms (0.15x)** | 7.5 ms | 45.5 ms |

> 表中为**纯执行时间**；一次性编译耗时另计（`clang -O2` 编译 IR ≈ 65–75 ms）。

## LLVM JIT（进程内，无编译产物）

若 `llvmlite` 可加载，`--jit` 走 MCJIT：**在同一进程内**把 IR 编译成机器码并调用。

| 端到端（含编译） | 耗时 |
|------------------|------|
| **JIT**（llvmlite MCJIT） | **~3.1 ms** |
| AOT（clang 编译 + 链接 + 起进程） | ~360 ms |

JIT 与 AOT 的**执行速度相同**（同为 LLVM 本机码），但省掉了 ~357 ms 的构建开销 ——
这正是 AI/Agent「生成即运行」循环最需要的形态：**本机码速度 + 零构建延迟**。

## 五条路径的定位

| 路径 | 命令 | 相对 CPython | 何时用 |
|------|------|--------------|--------|
| 解释 | `blacklang f.bl` | 35–57x 慢 | 开发/REPL/互操作，免编译、持久状态 |
| AOT→Python | `codegen.py` | ≈1.0x | 算法任务，零工具链依赖 |
| AOT→C | `--native f.bl` | 0.16–0.94x | 需要 C 源码可审计时（`--emit-c`） |
| **AOT→LLVM** | `--llvm f.bl` | **0.15–0.52x** | 生产热路径，最高性能 |
| **LLVM JIT** | `--jit f.bl` | 同 LLVM，**零构建** | AI 生成即运行、交互式 |

## 这条曲线说明了什么

```
解释(VM)      ~35–57x CPython   ← 纯解释开销
AOT→Python      ~1.0x CPython   ← Python 同级
AOT→C         0.16–0.94x        ← 越过 CPython
AOT→LLVM      0.15–0.52x        ← 最快，且优于手写 C（LLVM 优化器 > 手写循环）
LLVM JIT       同 LLVM，且无构建延迟
```

「介于 Python 与 C 之间」的目标已**达成并越过 Python 一侧**：编译路径（LLVM/C）比 CPython
快 2–6 倍，与手写 C 同量级甚至更快；解释路径服务开发体验。

## 支持的子集（LLVM / C 后端）

`int / float / string / bool`、变量、`+ - * / %`、`== != < > <= >=`、`and/or`、
`if/elif/else`、`while`、`for-in`(列表字面量)、函数定义与递归、`return`、`print`
（多参数空格分隔）、字符串拼接（含数值自动转字符串）、浮点数按**最短可回读**表示输出
（与 Python `repr` 一致）。

不支持（抛 `BL2001` 并**自动回退**到 VM）：`use` 互操作、字典、列表变量、`sandbox` 运行期语义。

## 运行

```bash
cd 创建新的编程语言
PYTHONPATH=src python3 benchmarks/bench.py          # 五路径全跑
PYTHONPATH=src python3 -m blacklang --llvm   <f>    # AOT→LLVM 原生运行
PYTHONPATH=src python3 -m blacklang --emit-llvm <f> # 查看 LLVM IR
PYTHONPATH=src python3 -m blacklang --native <f>    # AOT→C 原生运行
python3 -m blacklang --jit <f>                       # 进程内 JIT（需 llvmlite）
```

> **JIT 环境要求**：macOS 上「加固运行时」的 Python（如本仓库自带的运行时）会因
> library validation 拒绝加载 `llvmlite` 的 dylib。此时 `--jit` 会**明确提示并回退**。
> 用普通 `python3 -m venv` 建的环境即可启用真 JIT：
> ```bash
> python3 -m venv .venv && .venv/bin/pip install llvmlite
> .venv/bin/python -m blacklang --jit <f>
> ```
> 指针模型自动适配：LLVM ≥16 用 opaque pointer，LLVM ≤15 自动切换 typed pointer。
