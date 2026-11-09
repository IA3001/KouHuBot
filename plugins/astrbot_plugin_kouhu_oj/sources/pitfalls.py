"""C++ 算竞语法坑点题源。

坑点不是从某个 OJ 拉来的——各家题解博客零散、没有结构化接口，
硬爬质量不可控。所以这里内置一份人工整理的坑点库（pitfalls.json），
主要来源是 OI Wiki 的《常见错误》一文，另外补了几条高频的实践坑。

每条坑点是一段**有 bug 的代码**加标准答案，玩法是「找茬」：
群友看代码说错在哪，模型对照标准答案判分。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

_DEFAULT_DATA = Path(__file__).resolve().parent.parent / "pitfalls.json"


class PitfallSource:
    """内置坑点库，纯本地读取，不发网络请求。"""

    def __init__(self, data_file: str | Path | None = None) -> None:
        self._path = Path(data_file) if data_file else _DEFAULT_DATA
        self._items: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._items is None:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = []
            self._items = [item for item in raw if item.get("id") and item.get("buggy_code")]
        return self._items

    def count(self) -> int:
        return len(self._load())

    def categories(self) -> list[str]:
        seen: list[str] = []
        for item in self._load():
            cat = item.get("category") or "其他"
            if cat not in seen:
                seen.append(cat)
        return seen

    def get(self, pitfall_id: str) -> dict | None:
        for item in self._load():
            if item["id"] == pitfall_id:
                return item
        return None

    def random_pitfall(
        self, exclude: tuple[str, ...] = (), category: str | None = None
    ) -> dict:
        items = self._load()
        if not items:
            raise LookupError("坑点库是空的，检查 pitfalls.json")

        pool = [item for item in items if item["id"] not in set(exclude)]
        if category:
            key = category.strip().lower()
            matched = [
                item
                for item in pool
                if key in (item.get("category") or "").lower()
                or key in item["id"].lower()
            ]
            if matched:
                pool = matched
        if not pool:
            pool = items
        return random.choice(pool)
