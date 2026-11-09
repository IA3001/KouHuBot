"""LeetCode 中文站题目源。

走官方 GraphQL 接口而不是爬 HTML 页面，原因很实际：

- Codeforces 的题面页面会被 Cloudflare 拦（403）
- 洛谷的页面有反爬，直接 302 死循环
- LeetCode 的 GraphQL 是前端自己在用的接口，字段稳定、不带人机验证

实测 2026-11：`problemsetQuestionList` 返回 4497 道题，`question` 能拿到中文题面。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

GRAPHQL_URL = "https://leetcode.cn/graphql/"
PROBLEM_URL = "https://leetcode.cn/problems/{slug}/"

_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://leetcode.cn",
    "Referer": "https://leetcode.cn/problemset/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

_LIST_QUERY = """
query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList(categorySlug: $categorySlug, limit: $limit, skip: $skip, filters: $filters) {
    total
    questions {
      frontendQuestionId
      titleCn
      titleSlug
      difficulty
      paidOnly
    }
  }
}
"""

_DETAIL_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    title
    translatedTitle
    titleSlug
    difficulty
    content
    translatedContent
    isPaidOnly
    topicTags {
      name
      slug
    }
  }
}
"""

# 用户可能输入的难度写法 -> 接口里的枚举值
DIFFICULTY_ALIASES = {
    "e": "EASY",
    "easy": "EASY",
    "简单": "EASY",
    "m": "MEDIUM",
    "medium": "MEDIUM",
    "mid": "MEDIUM",
    "中等": "MEDIUM",
    "h": "HARD",
    "hard": "HARD",
    "困难": "HARD",
}

DIFFICULTY_LABEL = {"EASY": "简单", "MEDIUM": "中等", "HARD": "困难"}


class SourceError(Exception):
    """题目源不可用（网络、接口变更、抽不到题等）。"""


def normalize_difficulty(raw: str | None) -> str | None:
    """把用户输入的难度归一化成 EASY/MEDIUM/HARD，认不出就返回 None。"""
    if not raw:
        return None
    return DIFFICULTY_ALIASES.get(raw.strip().lower())


class LeetCodeSource:
    """按需拉题，只在内存里缓存，不落库。

    抽题策略：先拿总数，然后随机一个 skip 偏移拉一页（50 条），
    在页面里随机挑一道。这样每次抽题只需 2 次请求，
    不用把 4497 道题全拉下来。
    """

    PAGE_SIZE = 50
    TOTAL_TTL_SECONDS = 3600

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout
        self._total: int | None = None
        self._total_at = 0.0
        self._detail_cache: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    async def _post(self, query: str, variables: dict, operation: str) -> dict:
        try:
            import aiohttp
        except ImportError as exc:  # pragma: no cover - AstrBot 一定带 aiohttp
            raise SourceError("缺少 aiohttp 依赖，无法访问 LeetCode") from exc

        payload = {"query": query, "variables": variables, "operationName": operation}
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
                async with session.post(GRAPHQL_URL, json=payload) as resp:
                    if resp.status != 200:
                        raise SourceError(f"LeetCode 返回 HTTP {resp.status}")
                    data = await resp.json(content_type=None)
        except SourceError:
            raise
        except asyncio.TimeoutError as exc:
            raise SourceError("LeetCode 请求超时，稍后再试") from exc
        except Exception as exc:  # noqa: BLE001 - aiohttp 异常种类多，统一收口
            raise SourceError(f"LeetCode 请求失败：{exc}") from exc

        if data.get("errors"):
            message = (data["errors"][0] or {}).get("message") or "未知错误"
            raise SourceError(f"LeetCode 接口报错：{message}")
        return data.get("data") or {}

    async def _fetch_total(self) -> int:
        if self._total is not None and time.time() - self._total_at < self.TOTAL_TTL_SECONDS:
            return self._total
        data = await self._post(
            _LIST_QUERY,
            {"categorySlug": "", "limit": 1, "skip": 0, "filters": {}},
            "problemsetQuestionList",
        )
        block = data.get("problemsetQuestionList") or {}
        total = int(block.get("total") or 0)
        if total <= 0:
            raise SourceError("LeetCode 返回的题目总数为 0，接口可能变了")
        self._total = total
        self._total_at = time.time()
        return total

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    async def random_problem(
        self,
        difficulty: str | None = None,
        exclude: tuple[str, ...] = (),
        attempts: int = 6,
    ) -> dict:
        """随机抽一道非会员题，返回统一结构的 dict。"""
        want = normalize_difficulty(difficulty)
        total = await self._fetch_total()
        last_error: SourceError | None = None

        for _ in range(attempts):
            skip = random.randint(0, max(total - self.PAGE_SIZE, 0))
            data = await self._post(
                _LIST_QUERY,
                {"categorySlug": "", "limit": self.PAGE_SIZE, "skip": skip, "filters": {}},
                "problemsetQuestionList",
            )
            questions = ((data.get("problemsetQuestionList") or {}).get("questions")) or []
            pool = [
                q
                for q in questions
                if not q.get("paidOnly")
                and q.get("titleSlug")
                and q.get("titleSlug") not in exclude
                and (want is None or str(q.get("difficulty") or "").upper() == want)
            ]
            if not pool:
                continue
            chosen = random.choice(pool)
            try:
                return await self.problem_detail(chosen["titleSlug"])
            except SourceError as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise SourceError("没抽到合适的题目，稍后再试")

    async def problem_detail(self, slug: str) -> dict:
        if slug in self._detail_cache:
            return self._detail_cache[slug]

        data = await self._post(_DETAIL_QUERY, {"titleSlug": slug}, "questionData")
        question = data.get("question")
        if not question:
            raise SourceError(f"题目 {slug} 不存在")
        if question.get("isPaidOnly"):
            raise SourceError("抽到了会员题，换一道")

        content = question.get("translatedContent") or question.get("content") or ""
        real_slug = question.get("titleSlug") or slug
        detail = {
            "slug": real_slug,
            "frontend_id": str(question.get("questionFrontendId") or ""),
            "title": question.get("translatedTitle") or question.get("title") or real_slug,
            "difficulty": str(question.get("difficulty") or "").upper(),
            "content_html": content,
            "tags": [
                tag.get("name")
                for tag in (question.get("topicTags") or [])
                if tag.get("name")
            ],
            "link": PROBLEM_URL.format(slug=real_slug),
        }
        self._detail_cache[slug] = detail
        return detail

    def clear_cache(self) -> None:
        self._detail_cache.clear()
        self._total = None
        self._total_at = 0.0
