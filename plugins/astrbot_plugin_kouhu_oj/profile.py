"""个人主页的统计口径：CF 那套 rating / 热力图 / 今日本周本月。

这里只做纯计算，不碰数据库也不碰 HTML，方便单测：

- ``tier_of_points``：把积分反查回 CF 档位（分值表是一一映射的）。
- ``rank_of``：档位 → CF 风格段位（颜色 + 中文名），卡片上涂色用。
- ``build_profile``：把一串 AC 记录（正序）算成展示用的一组数字：rating、
  今日/本周/本月、连续天数、热力图格子、积分曲线、最近做过的题。

口径都写在返回值里，卡片只负责排版；改口径只改这一个文件。
"""

from __future__ import annotations

import math
import time

try:  # 插件按包加载时走相对导入，本地测试桩按顶层模块加载
    from .scoring import TIER_POINTS, LOWEST_TIER, HIGHEST_TIER
except ImportError:
    from scoring import TIER_POINTS, LOWEST_TIER, HIGHEST_TIER

# 分值 → 档位（TIER_POINTS 是一一映射；旧数据里可能有对不上的分值，返回 None）
POINTS_TIER: dict[int, int] = {points: tier for tier, points in TIER_POINTS.items()}

# CF 段位：阈值从低到高，取「不超过该 rating 的最大段位」。
# 颜色只写语义名，具体色值在卡片的 CSS 里，换配色不用改这里。
RANKS: tuple[tuple[int, str, str], ...] = (
    (0, "newbie", "新手"),
    (1200, "pupil", "入门"),
    (1400, "specialist", "熟练"),
    (1600, "expert", "专家"),
    (1900, "cm", "候选大师"),
    (2100, "master", "大师"),
    (2400, "gm", "宗师"),
)

# 少于这么多道 AC 就先不给 rating：一题定级会误导人。
RATING_MIN_SOLVES = 3

# 热力图默认铺 17 周（119 天 ≈ 4 个月），和 GitHub 个人页的观感接近。
HEATMAP_WEEKS = 26
WEEKDAY_NAMES = ("一", "二", "三", "四", "五", "六", "日")


def _longest_streak(days_with_solves: set[tuple[int, int, int]]) -> int:
    """历史最长连续做题天数（和「当前连续」不是一回事，两个都要显示）。"""
    best = run = 0
    previous = None
    for day in sorted(days_with_solves):
        stamp = time.mktime((*day, 0, 0, 0, 0, 0, -1))
        run = run + 1 if previous is not None and stamp - previous == 86400 else 1
        best = max(best, run)
        previous = stamp
    return best


def tier_of_points(points: int | None) -> int | None:
    """积分反查档位；对不上（旧数据/自定义分值）返回 None。"""
    try:
        return POINTS_TIER.get(int(points))
    except (TypeError, ValueError):
        return None


def rank_of(rating: int | None) -> tuple[str, str] | None:
    """rating → (语义色名, 中文段位名)。未定级返回 None。"""
    if rating is None:
        return None
    name = RANKS[0]
    for threshold, key, label in RANKS:
        if rating >= threshold:
            name = (threshold, key, label)
    return name[1], name[2]


def _day_start(stamp: float) -> float:
    """本地时区的当天 00:00（跟群里显示时间用同一套 localtime 口径）。"""
    local = time.localtime(stamp)
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                        0, 0, 0, 0, 0, -1))


def _day_key(stamp: float) -> tuple[int, int, int]:
    local = time.localtime(stamp)
    return local.tm_year, local.tm_mon, local.tm_mday


def _rating_of(tiers: list[int]) -> int | None:
    """稳定解出的档位 ≈ 已 AC 题目的平均档位，取整到 50。"""
    if len(tiers) < RATING_MIN_SOLVES:
        return None
    mean = sum(tiers) / len(tiers)
    # 显式四舍五入：round() 是银行家舍入，1325 会被压到 1300，看着像 bug。
    rating = int(math.floor(mean / 50.0 + 0.5) * 50)
    return max(LOWEST_TIER, min(HIGHEST_TIER, rating))


def _streak(days_with_solves: set[tuple[int, int, int]], now: float) -> int:
    """连续做题天数：从今天（今天没做就从昨天）往回数。"""
    day = _day_key(now)
    if day not in days_with_solves:
        day = _day_key(now - 86400)
        if day not in days_with_solves:
            return 0
    streak = 0
    stamp = time.mktime((*day, 0, 0, 0, 0, 0, -1))
    while _day_key(stamp) in days_with_solves:
        streak += 1
        stamp -= 86400
    return streak


def build_profile(solves: list[dict], *, now: float | None = None,
                  weeks: int = HEATMAP_WEEKS, recent: int = 8) -> dict:
    """把 AC 记录算成卡片要的一组数字。

    ``solves`` 每项要有 ``key`` / ``title`` / ``points`` / ``at``（时间戳），
    按时间正序。返回值里的 rating 可能是 None（题太少，先不定级）。
    """
    now = time.time() if now is None else now
    rows = sorted(solves, key=lambda r: r["at"])

    today = _day_key(now)
    today_start = _day_start(now)
    week_start = today_start - time.localtime(now).tm_wday * 86400
    month_start = time.mktime((today[0], today[1], 1, 0, 0, 0, 0, 0, -1))

    def window(start: float) -> tuple[int, int]:
        picked = [r for r in rows if r["at"] >= start]
        return len(picked), sum(int(r.get("points") or 0) for r in picked)

    tiers = [t for t in (tier_of_points(r.get("points")) for r in rows) if t]
    rating = _rating_of(tiers)
    rank = rank_of(rating)
    peak = max(((tier_of_points(r.get("points")), r) for r in rows
                if tier_of_points(r.get("points"))), default=None, key=lambda item: item[0])

    # -- 热力图：最后一列是本周，行是周一到周日 ----------------------
    today_stamp = _day_start(now)
    this_monday = today_stamp - time.localtime(now).tm_wday * 86400
    first_monday = this_monday - (weeks - 1) * 7 * 86400
    per_day: dict[tuple[int, int, int], int] = {}
    points_per_day: dict[tuple[int, int, int], int] = {}
    for row in rows:
        day = _day_key(row["at"])
        per_day[day] = per_day.get(day, 0) + 1
        points_per_day[day] = points_per_day.get(day, 0) + int(row.get("points") or 0)

    cells = []
    months: list[tuple[int, str]] = []
    last_month = None
    for week in range(weeks):
        week_start_stamp = first_monday + week * 7 * 86400
        monday = _day_key(week_start_stamp)
        if monday[1] != last_month:
            months.append((week, "%d月" % monday[1]))
            last_month = monday[1]
        for weekday in range(7):
            stamp = week_start_stamp + weekday * 86400
            day = _day_key(stamp)
            count = per_day.get(day, 0)
            cells.append({
                "date": day,
                "count": count,
                "level": 0 if not count else min(4, count),
                "future": stamp > today_stamp,
            })

    # -- 积分曲线：同一时间窗内的每日累计积分 ------------------------
    curve = []
    total_before = sum(int(r.get("points") or 0) for r in rows
                       if r["at"] < first_monday)
    running = total_before
    for day_index in range(weeks * 7):
        stamp = first_monday + day_index * 86400
        if stamp > today_stamp:
            break          # 曲线只画到今天，后面的格子留空，不预告未来
        day = _day_key(stamp)
        running += points_per_day.get(day, 0)
        curve.append({"day": day, "total": running})

    recent_rows = [{
        "key": r.get("key") or "",
        "title": r.get("title") or "",
        "at": r["at"],
        "points": int(r.get("points") or 0),
        "tier": tier_of_points(r.get("points")),
    } for r in reversed(rows[-recent:])]

    today_count, today_points = window(today_start)
    week_count, week_points = window(week_start)
    month_count, month_points = window(month_start)
    return {
        "solves": len(rows),
        "points": sum(int(r.get("points") or 0) for r in rows),
        "rating": rating,
        "rank": rank,
        "peak_tier": peak[0] if peak else None,
        "peak_key": (peak[1].get("key") or "") if peak else "",
        "today": {"count": today_count, "points": today_points},
        "week": {"count": week_count, "points": week_points},
        "month": {"count": month_count, "points": month_points},
        "streak": _streak(set(per_day), now),
        "longest_streak": _longest_streak(set(per_day)),
        "heatmap": {"cells": cells, "months": months, "weeks": weeks,
                    "max": max(per_day.values(), default=0)},
        "curve": curve,
        "recent": recent_rows,
        "active_days": len(per_day),
    }
