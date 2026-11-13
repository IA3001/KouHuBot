#!/usr/bin/env python3
"""取「只属于本插件」的更新日志，用于发布提交。

CHANGELOG.md 里同时记着另一个插件（astrbot_plugin_acm_notes）的版本，
那些小节还在开发中，不能进 kouhu 的发布提交——否则 `ver` 和历史答疑里
会冒出与口胡 OJ 无关的条目。

用法：
    python3 tools/release_changelog.py            # 打印发布用的日志正文
    python3 tools/release_changelog.py --write    # 顺手把内嵌副本也按这份正文重生成

发布提交的固定流程（保持提交里只有本插件的改动）：
    text=$(python3 tools/release_changelog.py)
    blob=$(printf '%s' "$text" | git hash-object -w --stdin)
    git update-index --cacheinfo 100644 "$blob" CHANGELOG.md
    python3 tools/release_changelog.py --write
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "CHANGELOG.md"
DST = ROOT / "plugins" / "astrbot_plugin_kouhu_oj" / "_changelog_text.py"

# 另一个插件的小节标题前缀。出现第一个就要开始截断。
FOREIGN_PREFIXES = ("## ACM 笔记助教", "## 新插件 astrbot_plugin_acm_notes")


def release_text(text: str) -> str:
    """去掉别的插件的小节，只留下 # 更新日志 头部与本插件的版本记录。"""
    start = None
    for prefix in FOREIGN_PREFIXES:
        index = text.find(prefix)
        if index != -1 and (start is None or index < start):
            start = index
    if start is None:
        return text
    # 截到下一个本插件版本标题（## x.y.z）为止，中间那段属于别的插件。
    resume = None
    probe = start + 1
    while True:
        candidate = text.find("\n## ", probe)
        if candidate == -1:
            break
        title = text[candidate + 1:text.find("\n", candidate + 1)].strip()
        rest = title[3:].strip()
        if rest and rest[0].isdigit() and "." in rest.split()[0]:
            resume = candidate + 1
            break
        probe = candidate + 1
    if resume is None:
        return text[:start].rstrip() + "\n"
    return text[:start] + text[resume:]


def _sync_module():
    spec = importlib.util.spec_from_file_location("sync_changelog", ROOT / "tools" / "sync_changelog.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    text = SRC.read_text(encoding="utf-8")
    release = release_text(text)
    if "--write" in argv:
        sync = _sync_module()
        DST.write_text(sync.HEADER + sync.repr_blocks(release) + "\n", encoding="utf-8")
        print(f"已按发布版重生成 {DST.relative_to(ROOT)}（{len(release)} 字符）", file=sys.stderr)
        return 0
    sys.stdout.write(release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
