#!/usr/bin/env python3
"""把 CHANGELOG.md 内嵌成 Python 模块，作为加载失败时的兜底。

为什么需要这个：更新日志是 .md 文件，而插件真正保证被复制的是 .py。
数据卷里如果只有代码是新的、.md 没跟着同步（或者部署方式根本不含 .md），
`ask 更新日志如何` 就会静默退回大模型，然后答「快照里没有这个字段」。

两个副本容易走样，所以有测试比对两者是否一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "CHANGELOG.md"
DST = ROOT / "plugins" / "astrbot_plugin_kouhu_oj" / "_changelog_text.py"

HEADER = '''"""由 tools/sync_changelog.py 生成，不要手改。

CHANGELOG.md 内嵌副本：部署时 .md 可能没跟着同步到数据卷，
这份 .py 一定在，用作兜底。
"""

CHANGELOG_MD = '''


def main() -> int:
    if not SRC.is_file():
        print(f"找不到 {SRC}", file=sys.stderr)
        return 1
    text = SRC.read_text(encoding="utf-8")
    DST.write_text(
        HEADER + repr_blocks(text) + "\n", encoding="utf-8"
    )
    print(f"已生成 {DST.relative_to(ROOT)}（{len(text)} 字符）")
    return 0


def repr_blocks(text: str) -> str:
    """用三引号包起来，但避开正文里可能出现的连续引号。"""
    quote = '"""'
    while quote in text:
        quote += '"'
    return f"{quote}{text}{quote}"


if __name__ == "__main__":
    raise SystemExit(main())
