# BlackLang

> 面向 AI 的下一代编程语言 —— **让 AI 快速、安全地做到任何想做的项目**

BlackLang 不是为人类程序员设计的，而是为一等公民——**大语言模型（AI）与自动化智能体**——设计的编程语言。

## 核心理念

| 支柱 | 说明 |
|------|------|
| ⚡ **快速** | 紧凑语法节省 token；AOT/JIT 编译，性能介于 Python 与 C 之间 |
| 🛡️ **安全** | 内存安全默认；权限作为语言特性；静态可审计 |
| 🔌 **互通** | 原生 FFI 直连 Python/JS/Go/Rust/C，统一心智调用前后端框架 |

## 文档

- 📄 [愿景书](docs/BlackLang愿景书.docx) —— 完整概念设计、技术架构、路线图
- 🗂 [语言规范草案](docs/语言规范草案.md) —— 语法与核心概念初稿
- 🛡 [安全模型](docs/安全模型.md) —— 三层防御：编译期拦截·运行期强制·跨进程墙
- 🤖 [AI 集成](docs/AI集成.md) —— 让 LLM/Agent 直接调用 BlackLang（LangChain Tool / API / CLI）
- 📊 [性能基准](benchmarks/RESULTS.md) —— 解释(VM) / AOT→Python / AOT→C / **AOT→LLVM** / JIT / CPython 对比

## 当前实现（MVP → 可交付后端）

已跑通**可执行**的解释器骨架，零依赖（纯 Python 标准库）：

```
src/blacklang/
  tokenizer.py   # 词法（注释、字符串转义、数字/标识符/关键词）
  parser.py      # 递归下降解析器（可省略分号、类型标注、属性/下标/字典/列表）
  ast_nodes.py   # AST 节点定义
  evaluator.py   # 树遍历求值器（sandbox 运行期强制 + Interop Bus 调用）
  checker.py     # ★ 编译期能力分析 + 自动生成能力清单
  typechecker.py # ★ Phase 2b：静态类型推断与编译期类型检查
  interop.py     # ★ Interop Bus：python 正向 FFI + node 远端代理
  node_bridge.py #   Node 宿主 JSON-RPC 子进程桥
  node_runner.mjs#   Node 侧 runner（load/get/call）
  vm.py          # ★ 字节码编译器 + 栈式 VM（性能路径，编译期通过后执行）
  codegen.py     # ★ AOT→Python 后端：AST 直译为 Python，≈CPython 速度
  cbackend.py    # ★ AOT→C 后端：AST → C 源码 → clang -O2 → 本机码（C 速度）
  llvmbackend.py # ★ AOT→LLVM 后端：AST → LLVM IR → clang/JIT；最快且支持进程内真 JIT
  values.py      # ★ 运行时值类型：StructInstance（struct 实例）/ BoundMethod
  sandbox.py     # ★ 跨进程沙箱：子进程隔离 + 超时墙（三层防御最外一层）
  diagnostics.py # ★ 结构化诊断：机器可读 JSON(code/line/hint)，AI 可自纠
  scaffold.py    # ★ --init 一键生成可运行项目骨架
  api.py         # ★ 面向 AI 的 Python API：compile_check/manifest/run/native_run/diagnose
  cli.py         # blacklang <f> / --native / --emit-c / --check[--json] / --manifest / --sandbox / --init / REPL
```

**已实现特性：** 变量/`let` 声明、算术、字符串（含 `+` 数值自动转字符串）、条件/循环、
递归函数、列表/字典、**`struct` 自定义类型（字段/方法/双访问）**、`==`/`!=`/`<` 比较、`sandbox` 权限强制（`readonly-fs`/`net`…）、
`use python:/node:` 互操作、字节码 VM、AOT→Python 编译后端、AOT→C 原生编译后端、
**AOT→LLVM 后端（含进程内真 JIT）**、跨进程沙箱、结构化 JSON 诊断。

**运行方式：**

```bash
cd 创建新的编程语言
PYTHONPATH=src python3 -m blacklang examples/hello.bl        # 运行文件(编译期通过才执行)
PYTHONPATH=src python3 -m blacklang --llvm <f>               # ★ AOT→LLVM 原生运行(最快)
PYTHONPATH=src python3 -m blacklang --emit-llvm <f>          # ★ 查看 LLVM IR
PYTHONPATH=src python3 -m blacklang --jit <f>                # ★ 进程内 LLVM JIT(需 llvmlite)
PYTHONPATH=src python3 -m blacklang --native <f>             # ★ AOT→C 编译成本机码运行
PYTHONPATH=src python3 -m blacklang --emit-c <f>             # ★ 查看生成的 C 源码
PYTHONPATH=src python3 -m blacklang examples/structs.bl      # ★ struct 建模示例
PYTHONPATH=src python3 -m blacklang examples/native_demo.bl  # ★ 计算密集示例(可用 --native 加速)
PYTHONPATH=src python3 -m blacklang examples/business_demo/src/main.bl  # ★ 端到端业务 demo
PYTHONPATH=src python3 -m blacklang --check <f>              # ★ 编译期检查(能力+类型)
PYTHONPATH=src python3 -m blacklang --check <f> --json       # ★ 结构化 JSON 诊断(AI 可读)
PYTHONPATH=src python3 -m blacklang --manifest <f>           # ★ 输出能力清单 JSON
PYTHONPATH=src python3 -m blacklang --sandbox <f> --timeout N # ★ 跨进程沙箱+超时墙
PYTHONPATH=src python3 -m blacklang --init <dir>            # ★ 一键生成可运行项目
PYTHONPATH=src python3 -m blacklang -i                      # REPL(多行/持久状态/互操作)
PYTHONPATH=src python3 benchmarks/bench.py                   # ★ 四路径性能基准
python3 -m unittest tests.test_blacklang -v                 # 123 项测试
```

**执行模型**：`blacklang f.bl` 会**先做编译期(能力+类型)检查，通过后才执行**——既保留
「安全默认」，又走性能路径。执行可三选一：

| 路径 | 命令 | 速度 | 何时用 |
|------|------|------|--------|
| 解释 | `blacklang f.bl` | 慢 ~40–57x CPython | 开发/REPL/互操作 |
| AOT→Python | `api.native_run` 前置/`codegen` | ≈ CPython | 算法任务，零工具链 |
| **AOT→C** | `blacklang --native f.bl` | C 速度（0.16–0.94x） | 需要可审计 C 源码 |
| **AOT→LLVM** | `blacklang --llvm f.bl` | **最快（0.15–0.52x）** | 生产热路径 |
| **LLVM JIT** | `blacklang --jit f.bl` | 同 LLVM，**零构建延迟** | AI 生成即运行 |

性能基线见 [benchmarks/RESULTS.md](benchmarks/RESULTS.md)（四路径对比 CPython 与手写 C）。

### 类型系统（Phase 2b）
参数/变量/返回可类型标注；编译期类型推断与检查，错误当场暴露：
```black
fn add(x: int, y: int) -> int: { return x + y; }   // 类型标注
run main: {
    let n: int = 5;          // 显式类型
    let pi: float = 3.14;
    print(n + pi);           // 类型提升 int+float->float
}
```
错误如 `let s: int = "x"`、`fn f(x:int)->string: { return x; }` → `--check` 直接报「类型错误」。

### 真正的互操作（Interop Bus，Phase 4）
`use python: {...}` 直连真实 Python 模块；`use node: {...}` 经 JSON-RPC 子进程调 Node：
```black
use python: { math, json };
run main: {
    print(math.sqrt(144));                    // -> 12.0
    let o = json.loads('{"a":1}');
    print(o["a"]);                            // -> 1
}
```
```black
use node: { path };
run main: { print(path.join("a","b")); }      // -> a/b（需本机有 node）
```

### 面向 AI 的工具链
- `python -m blacklang --manifest f.bl` → 能力清单 JSON（运行前自证、审计、CI）
- `from blacklang import compile_check, manifest, run` → Python API，
  且 **`run` 安全默认：静态检查不通过就不执行**。
- [examples/agent_integration.py](examples/agent_integration.py) → AI 代理/LangChain 工作流演示

### ★ 编译期能力分析（Phase 2a 核心）

「越权」不再是运行期崩溃，而是**编译期直接拒绝**，并自动输出能力清单：

```bash
$ python3 -m blacklang --check examples/secure_scope.bl
[BlackLang 编译期检查通过]
  能力清单 program_caps : ['net', 'readonly-fs']
  函数所需能力 per_fn    : {'load_config': ['readonly-fs']}

$ python3 -m blacklang --check examples/sandbox.bl     # http_get 出现在 readonly-fs 里
[BlackLang 编译期检查失败]
  ✗ 编译期越权拒绝: http_get 需要权限 'net'，但当前作用域未授予
```

`secure_scope.bl` 也给了真实可运行的例子：读文件放 `readonly-fs`、网络放 `net`、纯计算零权限——静态检查通过，运行也通过。

### 安全支柱实机演示（重点）

`sandbox.bl` 验证「权限作为语言特性」：

```black
run main: {
    sandbox readonly-fs: {
        let content = read("examples/hello.bl");   // ✅ 允许
    }
    sandbox readonly-fs: {
        let r = http_get("https://example.com");    // ❌ 拒绝（需要 net 权限）
    }
}
```
输出：读成功；`http_get` 抛 `越权调用: http_get 需要权限 'net'`。

### 示例程序
- [examples/hello.bl](examples/hello.bl) — 入门
- [examples/core.bl](examples/core.bl) — 核心语法（含递归 fib）
- [examples/sandbox.bl](examples/sandbox.bl) — 安全支柱演示
- [examples/interop.bl](examples/interop.bl) — 互通支柱占位

## 状态

**Phase 1 · MVP 完成** —— 词法→AST→求值器骨架可跑。下一步：细化文法（EBNF）、类型系统与真正的 Interop Bus。见[路线图](docs/BlackLang愿景书.docx)。