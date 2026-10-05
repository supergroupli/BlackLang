# 让 AI/Agent 直接用上 BlackLang

BlackLang 是为「AI 生成 → 安全执行 → 出结果」设计的。本页给真实 LLM/Agent
三种接入方式，从服务器到轻量逐步可选。

## 方式 1 · LangChain Tool（推荐给函数调用型 Agent）

`examples/blacklang_tool.py` 把 BlackLang 封装成一个 Tool：编译自检 → 运行 → 返回简单字典。

```python
# 装了 LangChain：直接挂到你的 agent 的工具列表
from examples.blacklang_tool import NAME, DESCRIPTION, ARGS_SCHEMA, blacklang_tool

# 等价于：
#   from langchain_core.tools import Tool
#   tool = Tool.from_function(blacklang_tool, name=NAME, description=DESCRIPTION)
```

结果字典形态（AI 无需懂 BlackLang 内部）：

```json
{"ok": true, "stdout": "15\n", "errors": [], "timed_out": false}
```

- 越权代码 → `{"ok": false, "intercepted": true, "errors": ["编译期越权拒绝: …"]}`
- 死循环/超时 → `{"ok": false, "timed_out": true}`（沙箱墙已终止子进程）

## 方式 2 · Python API（自建 Agent 循环 / 你自写编排）

```python
import blacklang as bl

# 只检查不运行（AI 生成后可自证）
r = bl.compile_check(source)        # -> {"ok", "errors", "capabilities"}
# 安全默认：编译期不过则不执行
r = bl.run(source)                  # -> {"ok", "stdout", "errors"}
# 能力清单（运行前自证 + 审计）
m = bl.manifest(source)             # -> program_caps / per_fn / verdict
# ★ 结构化诊断（机器可读，AI 可定位自纠）
d = bl.diagnose(source)             # -> {"ok", "diagnostics": [{code,line,col,message,hint,stage}]}
# ★ AOT→C 原生编译运行（C 速度；不支持则自动回退 VM，fallback=True）
r = bl.native_run(source)           # -> {"ok","stdout","errors","c_source","fallback"}
# ★ AOT→LLVM / 进程内 JIT（最快；JIT 零构建延迟）
r = bl.llvm_run(source)             # -> {"ok","stdout","errors","ir","ptr_mode"}
r = bl.llvm_run(source, use_jit=True)  # 需要 llvmlite
```

跨进程沙箱：
```python
from blacklang.sandbox import run_in_sandbox
r = run_in_sandbox(source, timeout=5.0)   # 超时墙 + 隔离
```

### 结构化诊断（推荐给 LLM）

错误不再是"一坨文字"，而是稳定结构，AI 可直接按 `code` 决策、按 `hint` 修复：

```json
{
  "ok": false,
  "diagnostics": [{
    "severity": "error",
    "code": "BL1001",
    "line": null, "col": null,
    "message": "编译期越权拒绝: http_get 需要权限 'net' …",
    "hint": "把该调用放进授予对应权限的 `sandbox <权限>: { … }` 内；或去掉该调用",
    "stage": "capability"
  }]
}
```

| 错误码 | 含义 | 阶段 |
|--------|------|------|
| `BL0001` | 词法/语法错误（带 line/col） | parse |
| `BL1001` | 能力越权（编译期拒绝） | capability |
| `BL1002` | 未定义名称 | type |
| `BL1003` | 类型不匹配 | type |
| `BL1004` | 函数返回类型不符 | type |
| `BL2001` | C 后端不支持的构造 | cbackend |
| `BL9001/9002` | 运行期错误 / 沙箱超时 | runtime |

## 方式 3 · CLI（命令行 / CI 脚本）

```bash
blacklang --check src/main.bl        # 编译期(能力+类型)检查
blacklang --check src/main.bl --json # ★ 结构化 JSON 诊断（喂给 LLM 自纠）
blacklang --manifest src/main.bl     # 输出能力清单 JSON
blacklang src/main.bl                # 安全运行（编译期通过才执行，VM 路径）
blacklang --native src/main.bl       # AOT→C 编译成本机码运行（C 速度）
blacklang --emit-c src/main.bl       # 查看生成的 C 源码（可审计）
blacklang --llvm src/main.bl         # ★ AOT→LLVM 原生运行（最快）
blacklang --emit-llvm src/main.bl    # ★ 查看 LLVM IR
blacklang --jit src/main.bl          # ★ 进程内 LLVM JIT（需 llvmlite，零构建）
blacklang --sandbox src/main.bl      # 子进程沙箱 + 超时墙
blacklang --init myproj              # 生成可运行项目骨架
```

### AI 自纠闭环（推荐流程）

```
LLM 生成 BL 源码
   ↓  blacklang --check --json   （或 bl.diagnose）
有 diagnostics? ── 有 ──→ 按 code/hint/line 改代码 ──→ 回到检查
   ↓ 无
blacklang --native（或 bl.native_run）执行 → 拿到 stdout
```

## 给 AI 的提示词（把 BlackLang 教给 LLM 的最小话术）

> 你可以写 BlackLang 程序替我干活：语法块用花括号、语句以分号结尾（可省略尾分号）、
> `fn name(params) { ... }` 定义函数、`run main: { ... }` 是入口、`let x = ...` 声明、
> `for i in list { }` / `while cond { }` 循环、`if/else` 分支、`use python: { json }` 互通。
> 需要读文件必须写在 `sandbox readonly-fs: { ... }` 内，网络在 `sandbox net: { ... }` 内，
> 越权编译期就会被拒。用 `print` 输出结果（字符串可以用 `+` 拼数，自动转字符串）。

## 端到端示例

`examples/business_demo/` 展示一个 AI 可产出的真实管线：
`读 JSON(use python json) → 清洗(滤 qty≤0) → 按品类聚合 → 报表输出`。
Ctrl_C 验证：

```bash
PYTHONPATH=src python3 -m blacklang.cli --check examples/business_demo/src/main.bl
PYTHONPATH=src python3 -m blacklang.cli examples/business_demo/src/main.bl   # VM 性能路径
```

期望：原始 8 单 → 有效 6 单 → 电子 8095.0 / 家纺 497.9 / 食品 255.0 / 合计 8847.9。

## 安全默认 & 性能路径

- `api.run` / `blacklang f` 默认 **编译期(能力+类型)通过才执行**，安全不在可选项中。
- 性能路径三选一：字节码 VM（开发/REPL）、AOT→Python（≈CPython）、
  **AOT→C（C 速度，`--native`）**，见 [benchmarks/RESULTS.md](../benchmarks/RESULTS.md)。

## 测试覆盖

`TestAiTool`（工具层正常/越权/超时）、`TestBusinessDemo`（端到端管线）、
`TestSandbox`（三层防御）、`TestCodegen`（AOT→Python）、`TestVM`（VM）、
`TestCBackend`（AOT→C 真编译）、`TestDiagnostics`（结构化诊断）、
`TestNativeAndApi`（native 与回退）、`TestCrossPathConsistency`（三路径输出一致）。