"""错题本（mistake book）：只读 review_* 的查询 + 群聊友好的紧凑纯文本渲染。

本模块是纯增量：不建表、不改 ``PRAGMA user_version``、不碰 scoring 表，
只读 ``ReviewStoreMixin.init_reviews()`` 已经建好的两张表：

    review_rounds (id, scope_id, problem_key, title, opened_at, closed_at, status)
    review_entries(id, round_id, user_id, submission, verdict, comment,
                   points, hint_level, at)

宿主 OjStore 只要多重继承 ``MistakeBookMixin`` 即可使用（本 mixin 不定义
``__init__``，所以 MRO 顺序无所谓）。调用方负责传正确的 scope：坑点线路就是
``scope + GRAMMAR_SUFFIX``（main.py 里现成的规则），本模块不另造规则。

口径：
- 只看该 scope 内该 user 的作答，按 ``problem_key`` 分组（跨多轮合并）。
- 只要有任何一条 AC，这道题就移出错题本。
- 该题在本 scope 的最新一轮仍 ``status='open'`` 时整题隐藏——没结束的题不
  能露出题号，和 history 的隐藏口径一致。
- ``due`` = 最近一次作答距今已经 >= ``due_days`` 天（默认 3 天）。

性能口径（大表）：SQLite 用一条窗口函数 CTE 把「分组 / 是否有 AC / 未过次数 /
首末时间 / 最新一条判词 / 该题最新一轮状态」一次算完，没有 N+1 子查询，也
不在 Python 里重扫记录；回到 Python 的只有「每个 problem_key 一行」，不是每条
作答一行。过滤条件 ``scope_id`` / ``user_id`` 命中的是 review_store 已有的索引
``review_entries_user_at(user_id, at DESC, id DESC)`` 和 ``review_rounds`` 上的
``UNIQUE(scope_id, problem_key, opened_at)``，条目 JOIN 轮次走 review_rounds 的
主键 rowid；``review_entries(round_id)`` 没有索引，但本查询从条目（按 user_id
索引）驱动、按 rowid 回查轮次，因此不受影响。

分页在 Python 里切：SQLite 对「窗口函数 CTE + 外层 ORDER BY」会走一条慢得多
的排序路径（实测 2000 题：整条 CTE 全量算完 11ms，加上外层 ORDER BY 后 250ms），
所以这里只取一次全量分组结果（一题一行），排好序再切片。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Iterable

# 坑点线路的题号前缀（与 main.py 的 PITFALL_PREFIX 保持一致）。
# 这里不 import main.py：本模块必须能被单独导入，避免循环依赖。
PITFALL_PREFIX = "grm:"

# 判词标签：本模块自带一份，理由同上（main.py 的 _verdict_label 不能反向依赖）。
VERDICT_LABELS = {
    "AC": "🟢 AC · 通过",
    "WA": "🔴 WA · 未通过",
    "PARTIAL": "🟡 PARTIAL · 部分通过",
    "GIVEUP": "⚪ GIVEUP · 放弃",
    "GAVE_UP": "⚪ GIVEUP · 放弃",
    "ERROR": "🟠 ERROR · 异常",
}

DEFAULT_PAGE_SIZE = 5
DEFAULT_DUE_DAYS = 3.0
COMMENT_LIMIT = 60      # 判词只留一行，约 60 字
CHUNK_LIMIT = 700       # 单条消息的软上限，超了按条目边界切块
_RULE = "━" * 16

# 一条 CTE 把所有口径一次算完，外面不再排序/分页（原因见模块 docstring）。
_SELECT_SQL = """
WITH latest_round AS (
    SELECT problem_key, title, status,
           ROW_NUMBER() OVER (
               PARTITION BY problem_key ORDER BY opened_at DESC, id DESC
           ) AS round_rank
    FROM review_rounds
    WHERE scope_id = ?
),
mine AS (
    SELECT r.problem_key,
           r.title AS round_title,
           e.verdict AS verdict,
           e.comment AS comment,
           e.at AS at,
           SUM(CASE WHEN e.verdict = 'AC' THEN 1 ELSE 0 END)
               OVER (PARTITION BY r.problem_key) AS ac_count,
           SUM(CASE WHEN e.verdict <> 'AC' THEN 1 ELSE 0 END)
               OVER (PARTITION BY r.problem_key) AS fail_count,
           MIN(e.at) OVER (PARTITION BY r.problem_key) AS first_at,
           ROW_NUMBER() OVER (
               PARTITION BY r.problem_key ORDER BY e.at DESC, e.id DESC
           ) AS entry_rank
    FROM review_entries AS e
    JOIN review_rounds AS r ON r.id = e.round_id
    WHERE r.scope_id = ? AND e.user_id = ?
),
kept AS (
    SELECT m.problem_key AS problem_key,
           COALESCE(NULLIF(lr.title, ''), NULLIF(m.round_title, ''),
                    m.problem_key) AS title,
           m.fail_count AS fail_count,
           m.at AS at,
           m.verdict AS verdict,
           m.comment AS comment,
           m.first_at AS first_at
    FROM mine AS m
    LEFT JOIN latest_round AS lr
           ON lr.problem_key = m.problem_key AND lr.round_rank = 1
    WHERE m.entry_rank = 1
      AND m.ac_count = 0
      AND COALESCE(lr.status, '') <> 'open'
)
SELECT problem_key, title, fail_count, at AS last_at,
       verdict AS last_verdict, comment AS last_comment, first_at
FROM kept"""


def _positive_int(value: Any, default: int) -> int:
    """宽容地取正整数：非法输入回落到默认值，绝不抛异常。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else 1


def _non_negative_int(value: Any, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else 0


def _due_days_value(value: Any) -> float:
    try:
        days = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DUE_DAYS
    if days != days:  # NaN
        return DEFAULT_DUE_DAYS
    return max(0.0, days)


class MistakeBookMixin:
    """错题本查询，复用宿主 OjStore 的 ``sqlite3.Row`` 连接。"""

    _conn: sqlite3.Connection

    def mistakes(
        self,
        scope: str,
        user_id: str,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        due_only: bool = False,
        due_days: float = DEFAULT_DUE_DAYS,
    ) -> tuple[list[dict], int]:
        """返回 ``(items, total)``，总数为过滤后的题数。

        - ``scope``/``user_id`` 决定看谁的、哪条线路（坑点线路由调用方传入
          ``scope + GRAMMAR_SUFFIX``）。
        - 已有 AC 的题、以及本 scope 最新一轮仍未结束的题都不出现。
        - 排序：到期的在前，同组内最近作答时间早的在前（最久没管的最靠前）。
        - ``due_only`` 只保留已到期；``due_days`` 是到期阈值（天）。
        - ``page``/``page_size`` 从 1 起，越界钳制（页码夹到最后一页），不抛异常。
        """
        page = _positive_int(page, 1)
        page_size = _positive_int(page_size, DEFAULT_PAGE_SIZE)
        threshold = _due_days_value(due_days)
        now = time.time()
        cutoff = now - threshold * 86400.0

        # 一次分组聚合：一题一行，不含每条作答；排序/去到期/分页都在下面做。
        rows = self._conn.execute(
            _SELECT_SQL, (scope, scope, user_id)).fetchall()

        items: list[dict] = []
        for row in rows:
            last_at = float(row["last_at"])
            due = last_at <= cutoff
            if due_only and not due:
                continue
            items.append({
                "problem_key": row["problem_key"],
                "cf_key": row["problem_key"],  # 兼容老命名
                "title": row["title"],
                "fail_count": int(row["fail_count"]),
                "last_at": last_at,
                "last_verdict": row["last_verdict"],
                "last_comment": row["last_comment"],
                "first_at": float(row["first_at"]),
                "days_since": max(0.0, (now - last_at) / 86400.0),
                "due": due,
            })

        # 到期的排前面；同组内最近作答时间早的在前（最久没管的最靠前）。
        items.sort(key=lambda item: (0 if item["due"] else 1,
                                     item["last_at"], item["problem_key"]))
        total = len(items)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return items[start:start + page_size], total


# ---------------------------------------------------------------- 纯文本渲染


def _clip_comment(text: str, limit: int = COMMENT_LIMIT) -> str:
    """判词压成一行并截断，群聊里不展开长篇。"""
    line = " ".join(str(text or "").split())
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def _ago(days: float) -> str:
    """把「距今多少天」写成人话：不足一天按小时，十天以上取整。"""
    days = max(0.0, float(days))
    if days < 1.0:
        return f"{max(1, int(round(days * 24.0)))} 小时"
    if days < 10.0:
        text = f"{days:.1f}"
        if text.endswith(".0"):
            text = text[:-2]
        return f"{text} 天"
    return f"{int(days)} 天"


def _redo_and_review(key: str) -> str:
    """给出重做/回看两条命令；坑点线路的题号带 ``grm:`` 前缀。"""
    if key.startswith(PITFALL_PREFIX):
        return ("重做：grm new（同分类再抽）"
                f"｜回看：history grm show {key[len(PITFALL_PREFIX):]}")
    return f"重做：new CF{key}｜回看：history show {key}"


def _item_block(item: dict) -> str:
    key = str(item.get("problem_key") or item.get("cf_key") or "未知题号")
    fails = _non_negative_int(item.get("fail_count"), 0)
    try:
        days = float(item.get("days_since") or 0.0)
    except (TypeError, ValueError):
        days = 0.0
    head = f"📌 {key} · 未过 {fails} 次 · 距今 {_ago(days)}"
    if item.get("due"):
        head += " · 该复习了"
    verdict = str(item.get("last_verdict") or "").upper()
    label = VERDICT_LABELS.get(verdict, f"🟠 {verdict}" if verdict else "🟠 未知判词")
    comment = _clip_comment(item.get("last_comment"))
    if verdict in ("GIVEUP", "GAVE_UP"):
        # 放弃时的判词是插件自己写的「主动放弃」，没有信息量，换成可执行的提示
        lines = [head, "   ⚪ 当时直接看了答案，重做一遍才算真的会"]
    elif comment:
        lines = [head, f"   {label} 判词：{comment}"]
    else:
        lines = [head, f"   {label}（没有留下判词）"]
    lines.append("   " + _redo_and_review(key))
    return "\n".join(lines)


def _page_command(command: str, due_only: bool) -> str:
    """翻页命令必须带上线路与 due 限定，否则第 2 页会跳到别的册子。"""
    base = (command or "mistakes").strip() or "mistakes"
    return f"{base} due" if due_only else base


def _empty_text(due_only: bool, command: str = "mistakes") -> list[str]:
    base = (command or "mistakes").strip() or "mistakes"
    if due_only:
        return [
            "📕 错题本 · 待复习\n" + _RULE
            + "\n目前没有到期的错题，都还没到复习时间，先忙别的也行。"
            + f"\n想看全部错题：{base}"
        ]
    return [
        "📕 错题本\n" + _RULE
        + "\n目前没有待复习的错题。"
        + "\n答错或放弃的题在题目结束后会进这里，AC 之后自动移出。"
        + f"\n只看到期的：{base} due"
    ]


def _navigation(page: int, pages: int, command: str = "mistakes") -> str:
    parts = []
    if page < pages:
        parts.append(f"下一页：{command} {page + 1}")
    if page > 1:
        parts.append(f"上一页：{command} {page - 1}")
    if not parts:
        return f"翻页：{command} [页码]（目前只有 1 页）"
    return "｜".join(parts)


def mistakes_text(
    items: Iterable[dict],
    *,
    page: int,
    pages: int,
    total: int,
    due_only: bool = False,
    command: str = "mistakes",
    hint: str = "",
) -> list[str]:
    """把一页错题渲染成纯文本块（不含 Markdown 记号）。

    返回的 ``list[str]`` 每个元素都是一条可直接发出的消息；条目多时按条目边界
    切块，末尾带翻页提示。每页 5 条时整体约 600~800 字。

    ``command`` 是不含 due 的线路命令（``mistakes`` 或 ``mistakes grm``），
    翻页提示会据此拼出完整命令：``mistakes grm 2``、``mistakes due 2``。
    ``hint`` 是一句可选的补充说明（例如当前还有题没结束、重做会先被挡下）。
    """
    page = _positive_int(page, 1)
    pages = max(_positive_int(pages, 1), page)
    total = _non_negative_int(total, 0)
    items = list(items or ())
    page_cmd = _page_command(command, due_only)

    if not items:
        return _empty_text(due_only, command)

    label = "错题本 · 待复习" if due_only else "错题本"
    blocks = [f"📕 {label} · {page}/{pages} 页 · 共 {total} 题\n" + _RULE]
    blocks.extend(_item_block(item) for item in items)

    chunks: list[str] = []
    current = ""
    for block in blocks:
        if current and len(current) + 2 + len(block) > CHUNK_LIMIT:
            chunks.append(current)
            current = block
        else:
            current = block if not current else f"{current}\n\n{block}"
    if current:
        chunks.append(current)

    nav = _navigation(page, pages, page_cmd)
    tail = f"{nav}\n{hint.strip()}" if (hint or "").strip() else nav
    if len(chunks[-1]) + 2 + len(tail) <= CHUNK_LIMIT:
        chunks[-1] = f"{chunks[-1]}\n\n{tail}"
    else:
        chunks.append(tail)
    return chunks
