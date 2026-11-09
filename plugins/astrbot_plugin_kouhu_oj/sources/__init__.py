"""题目源集合。

目前只有一个 LeetCode 中文站。想加别的源（Codeforces、牛客……），
只要实现 `random_problem()` 和 `problem_detail()` 两个方法，返回同样结构的
dict 就能直接接进 `main.py`。

统一返回结构：
    {
        "slug":         str,   # 源站内唯一标识
        "frontend_id":  str,   # 展示编号，如 "1"
        "title":        str,   # 中文标题
        "difficulty":   str,   # EASY / MEDIUM / HARD
        "content_html": str,   # 题面 HTML（渲染图片和存库都用它）
        "tags":       [str],   # 标签，可为空
        "link":         str,   # 原题链接
    }
"""

from .leetcode import (  # noqa: F401
    DIFFICULTY_LABEL,
    LeetCodeSource,
    SourceError,
    normalize_difficulty,
)

__all__ = [
    "DIFFICULTY_LABEL",
    "LeetCodeSource",
    "SourceError",
    "normalize_difficulty",
]
