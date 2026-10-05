# BlackLang 端到端业务 Demo：销售数据清洗 + 聚合报表

一个真实业务管线的完整示例，证明 BlackLang 能被真实 LLM/代理直接用来干活：

```
JSON(销售数据) → 读取/解析(json interop) → 清洗(过滤无效行) → 按品类聚合 → 报表输出
```

## 跑起来

```bash
cd 创建新的编程语言
# 编译期(能力+类型)自检
PYTHONPATH=src python3 -m blacklang.cli --check examples/business_demo/src/main.bl
# 运行（默认走字节码 VM 性能路径）
PYTHONPATH=src python3 -m blacklang.cli examples/business_demo/src/main.bl
# 或跨进程沙箱运行（隔离 + 超时墙 + 双保险）
PYTHONPATH=src python3 -m blacklang.cli --sandbox examples/business_demo/src/main.bl
```

## 它演示了什么

| 支柱 | 实现 |
|------|------|
| **互通** | `use python: { json }` 读取并解析 `data/sales.json` |
| **快速** | 纯 BL 函数 `is_valid / amount / category_total / valid_count` + 循环清洗聚合（可走编译路径加速） |
| **安全** | `read` 仅允许在 `sandbox readonly-fs: {}` 内；编译期与运行期双重约束 |

## 预期输出

```
== BlackLang 销售报表 ==
原始订单数 = 8
有效订单数 = 6
== 分品类销售额 ==
  电子 : 8095.0
  家纺 : 497.9
  食品 : 255.0
== 合计 ==
总销售额 = 8847.9
报表完成。
```

> 数据校验：8 条原始订单，3 条无效（qty≤0 的两条：id3 qty=-1、id6 qty=0），
> 保留 6 笔有效。电子=1200*3+899*5=8095；家纺=99.9+199*2=497.9；食品=15.5*10+25*4=255；
> 合计=8847.9。