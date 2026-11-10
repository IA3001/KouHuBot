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

    def levels(self) -> dict[int, int]:
        """{难度: 条数}，给 status / 帮助提示用。"""
        out: dict[int, int] = {}
        for item in self._load():
            lv = int(item.get("level") or 2)
            out[lv] = out.get(lv, 0) + 1
        return dict(sorted(out.items()))

    def resolve_category(self, token: str) -> str | None:
        """把群友打的分类名映射到标准分类。

        支持中文全名、中文简写、拼音首字母，因为群里打字习惯统一不了：
        「输入输出」「输入」「io」「sr」都应该能命中输入输出。
        """
        token = (token or "").strip().lower()
        if not token:
            return None
        aliases = {
            "输入输出": ("输入输出", "输入", "io", "srsc", "sr", "inout"),
            "整数与溢出": ("整数", "溢出", "int", "overflow"),
            "数组与内存": ("数组", "内存", "array", "mem"),
            "排序与比较器": ("排序", "比较器", "sort", "cmp"),
            "STL": ("stl", "容器", "container"),
            "字符串": ("字符串", "string", "str"),
            "宏与预处理": ("宏", "预处理", "macro", "define"),
            "运算符": ("运算符", "运算", "operator", "op"),
            "数学": ("数学", "math"),
            "图论": ("图论", "图", "graph"),
            "递归与栈": ("递归", "栈", "recursion", "stack"),
            "二分": ("二分", "binary", "bs"),
            "复杂度": ("复杂度", "复杂", "complexity", "complex"),
            "多组数据": ("多组", "多测", "multi"),
        }
        for real, names in aliases.items():
            if token == real.lower():
                return real
            for n in names:
                if token == n.lower():
                    return real
        # 兜底：包含关系
        for item in self._load():
            cat = item.get("category") or ""
            if cat and (token in cat.lower() or cat.lower() in token):
                return cat
        return None

    def random_pitfall(
        self,
        exclude: tuple[str, ...] = (),
        category: str | None = None,
        level: int | None = None,
        level_max: int | None = None,
    ) -> dict:
        """随机抽一条。

        category 支持别名（见 resolve_category）；
        level 精确匹配，level_max 表示「不超过这个难度」。
        过滤后为空时**不静默退回到全部题库**——那样群友明明指定了「图论 L3」，
        拿到一道输入输出的题，会以为插件坏了。直接报错让调用方告诉用户。
        """
        items = self._load()
        if not items:
            raise LookupError("坑点库是空的，检查 pitfalls.json")

        pool = [item for item in items if item["id"] not in set(exclude)]
        if category:
            real = self.resolve_category(category) or category
            pool = [item for item in pool if item.get("category") == real]
        if level is not None:
            pool = [item for item in pool if int(item.get("level") or 2) == int(level)]
        elif level_max is not None:
            pool = [item for item in pool if int(item.get("level") or 2) <= int(level_max)]

        if not pool:
            raise LookupError("这个条件下没有坑点题，换个分类或难度试试")
        return random.choice(pool)

    def count(
        self,
        exclude: tuple[str, ...] = (),
        category: str | None = None,
        level: int | None = None,
    ) -> int:
        """符合条件的题还有多少条。用来判断「这一类出完了」还是「根本没这类」。"""
        pool = self._filter(exclude=exclude, category=category, level=level)
        return len(pool)

    def _filter(
        self,
        exclude: tuple[str, ...] = (),
        category: str | None = None,
        level: int | None = None,
    ) -> list[dict]:
        items = self._load()
        skip = set(exclude)
        pool = [item for item in items if item["id"] not in skip]
        if category:
            real = self.resolve_category(category) or category
            pool = [item for item in pool if item.get("category") == real]
        if level is not None:
            pool = [item for item in pool if int(item.get("level") or 2) == int(level)]
        return pool

    def get(self, pid: str) -> dict | None:
        for item in self._load():
            if item["id"] == pid:
                return item
        return None

    def category_summary(self) -> str:
        """一行一类的概览，直接贴进群消息。

        比原来的 L1122233 好读：那串数字需要看底部的注释才懂。
        """
        items = self._load()
        by_cat: dict[str, list[dict]] = {}
        for item in items:
            by_cat.setdefault(item.get("category") or "其他", []).append(item)

        levels = {1: "入门", 2: "常见", 3: "阴间"}
        proven = sum(1 for i in items if i.get("verified"))
        lines = [f"坑点题共 {len(items)} 道，按分类（{proven} 道经机器验证）："]
        for cat in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c)):
            group = by_cat[cat]
            per = []
            for lv in (1, 2, 3):
                n = sum(1 for i in group if int(i.get("level") or 2) == lv)
                if n:
                    per.append(f"{levels[lv]}{n}")
            lines.append(f"  {cat} {len(group)} 道（{' '.join(per)}）")
        lines.append("")
        lines.append("抽题：grm new [分类] [难度 1-3]，分类和难度都可以不带")
        return "\n".join(lines)
