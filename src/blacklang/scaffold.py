# -*- coding: utf-8 -*-
"""BlackLang 项目脚手架：`blacklang --init <dir>` 一键生成 AI 友好的可运行项目。"""
from __future__ import annotations

import os
from typing import List, Tuple

MAIN_BL = '''// BlackLang 最小可运行入口
// 运行: blacklang src/main.bl   （或黑标 --init 之后照此风格扩展）

fn add(a: int, b: int) -> int: {
    return a + b;
}

run main: {
    let name = "BlackLang";
    print("Hello, " + name + "!");
    print("add(3, 4) =", add(3, 4));

    // 文件读取放在 readonly-fs 沙箱内（安全默认）
    sandbox readonly-fs: {
        let cfg = read("config.json");
        print("config =", cfg);
    }
}
'''

CONFIG_JSON = '''{
  "name": "BlackLang demo",
  "version": "0.1",
  "greeting": "欢迎来到 BlackLang"
}
'''

README_STUB = '''# {name}

用 `blacklang --init` 生成的最小可运行项目。

## 运行
```bash
blacklang src/main.bl          # 运行
blacklang --check src/main.bl  # 编译期检查（能力 + 类型）
blacklang --manifest src/main.bl  # 输出能力清单 JSON
```

## 结构
- `src/main.bl`  程序入口（AI 通常从这里开始写逻辑）
- `config.json`  只读配置文件（演示 sandbox readonly-fs）
'''

# 生成的文件集合：(相对路径, 内容)
TEMPLATES: List[Tuple[str, str]] = [
    ("src/main.bl", MAIN_BL),
    ("config.json", CONFIG_JSON),
    ("README.md", README_STUB),
]


def scaffold(dirpath: str, name: str) -> List[str]:
    """在 dirpath 下生成 BlackLang 项目骨架，返回创建的文件路径列表。"""
    os.makedirs(dirpath, exist_ok=True)
    created: List[str] = []
    for rel, content in TEMPLATES:
        path = os.path.join(dirpath, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if rel == "README.md":
            content = content.format(name=name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        created.append(path)
    return created