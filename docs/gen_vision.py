# -*- coding: utf-8 -*-
"""生成 BlackLang 愿景书文档 (docs/BlackLang愿景书.docx)"""
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

BLACK = RGBColor(0x1A, 0x1A, 0x1A)
ACCENT = RGBColor(0x33, 0x33, 0x99)
GRAY = RGBColor(0x66, 0x66, 0x66)


def set_east_asia(run, name="PingFang SC"):
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = rPr.makeelement(qn("w:rFonts"), {})
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), name)


def p(doc, text, style=None, color=None, size=None, bold=None, align=None, space_after=6):
    para = doc.add_paragraph(style=style)
    run = para.add_run(text)
    if color is not None:
        run.font.color.rgb = color
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if align is not None:
        para.alignment = align
    para.paragraph_format.space_after = Pt(space_after)
    set_east_asia(run)
    return para


def bullet(doc, text, level=0, bold_prefix=None):
    para = doc.add_paragraph(style="List Bullet")
    if bold_prefix:
        r = para.add_run(bold_prefix)
        r.bold = True
        set_east_asia(r)
    run = para.add_run(text)
    set_east_asia(run)
    para.paragraph_format.space_after = Pt(4)
    for r in para.runs:
        set_east_asia(r)
    return para


doc = Document()

# 设置默认字体
style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)
style.element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")

# 封面标题
title = p(doc, "BlackLang", size=40, bold=True, color=BLACK, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
sub = p(doc, "面向 AI 的下一代编程语言 —— 让 AI 快速、安全地做到任何想做的项目",
        size=15, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
p(doc, "愿景书 · Vision · 概念设计", size=12, color=ACCENT,
  align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
doc.add_paragraph()
p(doc, "v0.1 · 概念草案", size=9, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)

doc.add_page_break()

# ===== 1. 定位与核心理念 =====
doc.add_heading("一、定位与核心理念", level=1)
p(doc, "BlackLang 是一门……不是为人类程序员，而是为大语言模型（AI）及其他自动化智能体设计的一门编程语言。它把“AI 能快速上手、安全地完成任意项目”作为第一设计目标，性能目标锁定在 Python 与 C 之间，并用极简统一的互操作机制串起前后端常见语言与框架。")

# ===== 2. 为什么需要一门“面向 AI”的语言 =====
doc.add_heading("二、为什么需要一门“面向 AI”的语言", level=1)
p(doc, "现有主流语言（Python / JavaScript / Go / Rust 等）都是为人类作者设计的人类中心语言。当 AI 成为主要作者时，它们暴露出系统性摩擦：", space_after=4)
bullet(doc, "人类语言为“可读”而冗余（命名、注释、空行），对 AI 是纯开销，却占用有限的上下文窗口。")
bullet(doc, "类型与错误处理、内存管理的隐式约定靠“惯例”而非“规则”，AI 难以稳定推断。")
bullet(doc, "生态碎片化——前端 JS/TS、后端 Go/Node、数据 Python、系统侧 C/Rust，AI 每次换栈都要重新学习一套心智模型。")
bullet(doc, "缺少“安全默认”，AI 生成的代码容易踩内存、权限、资源泄漏的坑，且难以被自动审计。")

p(doc, "BlackLang 想要回答的问题很简单：如果作者是 AI，而不是人，语言应该长什么样？")

# ===== 3. 三大支柱 =====
doc.add_heading("三、三大设计支柱", level=1)

doc.add_heading("支柱一：快速（Speed / Velocity）", level=2)
bullet(doc, "上下文高效：紧凑语法、无冗余样板，令 AI 在相同 token 预算下生成更多有效逻辑。")
bullet(doc, "声明式默认：常见任务（HTTP、文件、数据库、进程、并发）一行搞定，减少往返。")
bullet(doc, "编译即运行：AOT / JIT 生成机器码，启动快、执行快，性能介于 Python 与 C 之间。")
bullet(doc, "可注入工具链：AI 直接产出可编译、可测试、可回滚的单元，而非“近似”的代码片段。")

doc.add_heading("支柱二：安全（Safety）", level=2)
bullet(doc, "类型与内存安全默认：所有权/借用（参考 Rust），但由编译器与 AI 协作，避免人类心智负担。")
bullet(doc, "权限即语言特性：沙箱、网络访问、文件系统访问、进程权限都成为语法级能力标记。")
bullet(doc, "静态审计内建：安全策略随代码一起编译，CI 中自动校验，AI 无法静默越权。")
bullet(doc, "可回滚与确定性：可复现构建，AI 的每次改动都可追踪、可回退。")

doc.add_heading("支柱三：互通（Interop）", level=2)
bullet(doc, "调用一切：原生 FFI 直连 Python、Node/JS、Go、Rust、C/C++，双向互操作。")
bullet(doc, "框架即库：不重造轮子，直接以“库”形态封装 React/Vue、Express/FastAPI、NumPy/Pandas 等生态。")
bullet(doc, "统一心智：无论底层是什么，调用界面都是一种语法，AI 无需切换语言模型。")
bullet(doc, "宿主与嵌入：可作为胶水语言嵌入现有系统，反向也可被 JSON/HTTP/接口调用。")

# ===== 4. 技术架构草案 =====
doc.add_heading("四、技术架构草案", level=1)
p(doc, "以下为概念级架构，用于支撑“Python 与 C 之间的性能”和“调用任意语言框架”两大能力。", space_after=6)

doc.add_heading("4.1 编译与执行管线", level=2)
bullet(doc, "前端：紧凑文法 → AST（附加 Span，便于 AI 定位）。")
bullet(doc, "中端：类型检查 + 所有权分析 + 安全策略（能力标注）检查  → MIR。")
bullet(doc, "后端：LLVM AOT 编译本机码（靠近 C 的性能）；另提供解释器/JIT 模式用于临时脚本（靠近 Python 的便利）。")
bullet(doc, "产物：可执行文件 / 字节码 / WASM，三种形态按部署环境选择。")

doc.add_heading("4.2 互操作层（Interop Bus）", level=2)
bullet(doc, "声明式绑定：通过清单声明要调用的语言/框架/库及其版本，BlackLang 自动生成桥接代码。")
bullet(doc, "运行时桥：用 FFI（C ABI）+ 宿主嵌入（如 Python 解释器内嵌、Node 子运行时）实现双向调用。")
bullet(doc, "统一类型映射：JSON 为默认数据交换，任何异构类型自动序列化/反序列化。")
bullet(doc, "异步与并发：跨语言调用纳入统一 async/await 模型，不阻塞宿主事件循环。")

# ===== 5. 设计原则对比 =====
doc.add_heading("五、与主流语言的设计对比", level=1)
t = doc.add_table(rows=1, cols=4)
t.style = "Light Grid Accent 1"
hdr = t.rows[0].cells
for i, h in enumerate(["维度", "Python", "C", "BlackLang"]):
    hdr[i].text = h
rows = [
    ("主要作者", "人", "人", "AI"),
    ("语法密度", "中等", "低", "高（紧凑）"),
    ("性能", "慢（解释）", "快（编译）", "中（AOT/JIT）"),
    ("内存安全", "GC，安全", "手动，危险", "所有权，默认安全"),
    ("权限控制", "无内建", "无内建", "语法级能力标注"),
    ("跨语言互联", "需手动 ctypes/Cython", "需手动 ABI", "内建 Interop Bus"),
    ("可复现构建", "弱", "中", "强（默认）"),
]
for r in rows:
    cells = t.add_row().cells
    for i, v in enumerate(r):
        cells[i].text = v

doc.add_paragraph()

# ===== 6. 应用场景 =====
doc.add_heading("六、典型应用场景", level=1)
bullet(doc, "AI Agent / 自主编程：AI 直接生成 BlackLang，编译后执行并回滚，无需人工审阅细节。")
bullet(doc, "全栈项目脚手架：一次生成前后端 + 数据库 + 部署配置，性能逼近原生。")
bullet(doc, "数据处理与科学计算：内建桥接 NumPy/Pandas，向量运算走 C。")
bullet(doc, "平台插件与胶水层：把现有系统的各个语言模块缝合成一个可部署单元。")
bullet(doc, "沙箱实验：AI 在高隔离、低权限的 RunMode 下自由试错，不碰主机核心能力。")

# ===== 7. 别名与口号 =====
doc.add_heading("七、品牌与口号候选", level=1)
p(doc, "最终定位句（可选）：", bold=True)
bullet(doc, "“让 AI 能跑得更快，出事更少。”")
bullet(doc, "“把世界连起来，让 AI 跑起来。”")
bullet(doc, "“为一『黑盒』智能设计的全栈语言。”")

# ===== 8. 路线图 =====
doc.add_heading("八、路线图（草案）", level=1)
t2 = doc.add_table(rows=1, cols=3)
t2.style = "Light Grid Accent 1"
hdr2 = t2.rows[0].cells
for i, h in enumerate(["阶段", "目标", "关键交付"]):
    hdr2[i].text = h
phases = [
    ("Phase 0 · 概念", "锁定语法与互操作模型", "本文档 + 语言规范草案 + 示例程序"),
    ("Phase 1 · MVP", "从源码到可执行的解释器", "词法/语法/AST + 简单求值器跑通 HelloWorld 与算数"),
    ("Phase 2 · 类型+安全", "类型系统与能力标注", "所有权检查 + 权限编译期校验"),
    ("Phase 3 · 性能", "AOT/JIT 后端", "LLVM 后端，benchmark 对比 Python/C"),
    ("Phase 4 · 融合", "Interop Bus 打通生态", "首个 FFI 桥（Python + Node + C）"),
    ("Phase 5 · 社区", "开源与工具链", "CLI、包管理器、AI 插件、IDE/插件支持"),
]
for r in phases:
    cells = t2.add_row().cells
    for i, v in enumerate(r):
        cells[i].text = v

doc.add_paragraph()
p(doc, "下一步：先把 Phase 0 做实——产出语言规范草案与 HelloWorld 示例，再动手搭 MVP 解释器骨架。", color=GRAY, size=10)

# ===== 9. 设计理念演进 =====
doc.add_page_break()
doc.add_heading("九、新增设计理念（持续演进）", level=1)
p(doc, "在 MVP 与静态检查落地过程中，为强化「省事 + 安全」主张，新增两条设计理念：", space_after=4)

doc.add_heading("9.1 分层语法：高频直觉，高风险显式", level=2)
p(doc, "普通逻辑（计算、数据结构、控制流）用「极简直觉语法」，与 AI 的心智模型对齐，AI 少想也能写对、少写也能省 token；而高风险操作（文件、网络、权限、跨语言互操作）强制显式标注。危险永远醒目，安全默认。")

doc.add_heading("9.2 能力清单（Capability Manifest）：一次审核，永远复用", level=2)
p(doc, "编译器自动生成每个程序、每个函数的「能力清单」——谁碰了网络、谁读了文件，一清二楚，且编译期校验。AI 做项目前声明「我要碰什么」，编译器严格执行；审核一次即可放心复用，杜绝「逃逸的越权」。")

doc.add_heading("9.3 打包产物可产出自证", level=2)
p(doc, "面向 AI 的代码应自带「举证材料」——编译期输出能力清单、依赖清单、可复现构建指纹。让 AI 生成的项目「自带安全证书」，可审计、可回滚，而非黑盒。")

doc.add_paragraph()
p(doc, "以上理念已部分落地：静态检查器 checker.py（Phase 2a）已实现编译期越权拒绝与能力清单生成；分层语法与构建指纹随类型系统（Phase 2b）推进。", color=GRAY, size=10)

# ===== 10. 落地进度 =====
doc.add_heading("十、落地进度（截至当前）", level=1)
t3 = doc.add_table(rows=1, cols=3)
t3.style = "Light Grid Accent 1"
hdr3 = t3.rows[0].cells
for i, h in enumerate(["阶段", "状态", "说明"]):
    hdr3[i].text = h
rows3 = [
    ("Phase 1 · MVP 解释器", "✅ 完成", "词法→解析→求值器可运行；sandbox 运行期强制；20+ 测试"),
    ("Phase 2b · 类型系统", "✅ 完成", "静态类型推断/检查、`--check` 报类型错误、>6 类型检查测试"),
    ("Phase 4 · Interop Bus", "✅ 完成", "python 正向 FFI（math/json…）+ node JSON-RPC 子进程桥"),
    ("AI 工具链", "✅ 完成", "--manifest JSON、Python API、Agent/LangChain 集成示例"),
    ("Phase 3 · 字节码 VM", "✅ 完成", "AST→字节码→栈式 VM；执行前编译期(能力+类型)通过才运行；基准对比 CPython"),
    ("Phase 3 · AOT→Python", "✅ 完成", "AST 直译 Python：≈1.0x CPython（介于 Python 与 C）"),
    ("Phase 3 · AOT→C", "✅ 完成", "AST→C 源码 + clang -O2 本机码：0.13–0.63x CPython（C 速度，达成目标坐标）"),
    ("打包与脚手架", "✅ 完成", "pip install -e 安装为 blacklang 命令、--init、增强 REPL、四路径 benchmark"),
    ("安全纵深", "✅ 完成", "三层防御：编译期拦截·运行期强制·跨进程沙箱(--sandbox 子进程+超时墙)"),
    ("AI 生态落地", "✅ 完成", "LangChain Tool(blacklang_tool) + 端到端业务 demo(读JSON→清洗→聚合→报表)"),
    ("AI 可机读诊断", "✅ 完成", "结构化诊断 BL0001/1001/1002/… + line/col/hint，--check --json，LLM 可自纠"),
    ("Phase 3 · AOT→LLVM", "✅ 完成", "AST→LLVM IR→clang 本机码：fib 0.52x CPython、循环 0.15x，快于手写 C"),
    ("Phase 3 · LLVM JIT", "✅ 完成", "llvmlite MCJIT 进程内 JIT：同 LLVM 速度且零构建(3.1ms vs AOT 360ms)"),
    ("Phase 4 剩余", "⏭ 待推进", "所有权/借用模型、struct/match/错误处理/闭包/标准库/模块/并发"),
]
for r in rows3:
    cs = t3.add_row().cells
    for i, v in enumerate(r):
        cs[i].text = v

doc.add_paragraph()
p(doc, "当前测试：111 项全部通过；示例全跑通含 business_demo / native_demo；五条执行路径(VM / AOT→Python / AOT→C / AOT→LLVM / JIT)，跨路径输出逐字节一致；sandbox 越权编译期拦截、死循环超时墙终止；结构化 JSON 诊断可供 LLM 机读自纠。", color=GRAY, size=10)

doc.save("/Users/superlee/Documents/deepseek-harness/创建新的编程语言/docs/BlackLang愿景书.docx")
print("saved")