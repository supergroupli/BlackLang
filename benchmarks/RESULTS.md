# BlackLang 性能基准（可复现）

> 目标坐标：**介于 Python 与 C 之间**。本页记录四条执行路径的可复现基线。

同一台机器（Apple Silicon / Apple clang 21 / CPython 3.12）上，`python3 benchmarks/bench.py`
测得（越小越快）。`比率 = BlackLang 时间 / CPython 时间`。

| 工作负载 | 解释(VM) | AOT→Python | **AOT→C** | 手写 C 参照 | CPython |
|----------|----------|------------|-----------|-------------|---------|
| `fib(26)` 递归 | ~402 ms (56x) | ~7.2 ms (1.01x) | **~4.5 ms (0.63x)** | ~9.1 ms | ~7.1 ms |
| `while 200万` 算术 | ~1740 ms (41x) | ~42 ms (1.00x) | **~5.6 ms (0.13x)** | ~4.0 ms | ~42 ms |

> AOT→C 仅计**执行时间**（一次性 `clang -O2` 编译 46–52 ms 另计，见 benchmark 输出）。

## 四条路径的定位

| 路径 | 实现 | 速度 | 适用 |
|------|------|------|------|
| **解释** | `vm.py` 字节码栈式 VM | 比 CPython 慢 ~40–57x | 开发/REPL/互操作，免编译、持久状态 |
| **AOT→Python** | `codegen.py` 直译 Python | ≈ CPython（1.0x） | 算法繁重任务，零工具链依赖 |
| **AOT→C** | `cbackend.py` 生成 C + clang -O2 | **0.1–0.6x CPython（C 速度）** | 生产热路径，真正"逼近 C" |
| 参照 | CPython / 手写 C | 1.00x / 基准 | 坐标锚点 |

## 这条曲线说明了什么

```
解释(VM)   ~41–57x CPython   ← 纯解释开销
AOT→Python   ~1.0x CPython   ← Python 同级
AOT→C      0.13–0.63x CPython ← 已越过 CPython，落在 C 侧
```

CPython 通常比单线程 C 慢 20–50x。BlackLang 的 **AOT→C 路径已把相对 CPython 的比率
压到 1.0 以下**（即比 CPython 更快），与手写 C 处于同一量级 —— 这正是
「介于 Python 与 C 之间」目标的**达成证据**：解释路径服务开发体验，编译路径产出 C 级性能。

## AOT→C 支持的子集

`int / float / string / bool`、变量、`+ - * / %`、`== != < > <= >=`、`and/or`、
`if/elif/else`、`while`、`for-in`(列表字面量)、函数定义与递归、`return`、`print`、
字符串拼接(含数值自动转字符串)。

不支持（会给出 `BL2001` 诊断并**自动回退**到 VM）：`use` 互操作、字典、列表变量、
`sandbox` 运行期语义。查看生成代码：`blacklang --emit-c <f>`；原生运行：`blacklang --native <f>`。

## 运行

```bash
cd 创建新的编程语言
PYTHONPATH=src python3 benchmarks/bench.py        # 四路径全跑
PYTHONPATH=src python3 benchmarks/bench.py fib     # 只跑 fib
PYTHONPATH=src python3 -m blacklang --native  <f>  # AOT→C 原生运行
PYTHONPATH=src python3 -m blacklang --emit-c  <f>  # 查看生成的 C
```

> 无 C 编译器时，`--native` 与 `native_run()` 会**明确提示并回退**到 VM 路径，不会失败。