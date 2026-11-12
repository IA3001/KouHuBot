"""积分规则：档位表、每题分值、群难度进度。

这里只有纯计算，不碰网络也不碰数据库，方便单测。

设计要点：
- 题目按 CF rating 分档，每档预先定一个分值，分值近似每档 x1.4 几何增长。
- 玩家总分 = 已解出题目的分值之和，只计首次 AC。
- 每10道已结束的题评估一次群档位；本轮解出题数除以派题数决定升降。
"""

from __future__ import annotations

# 档位 -> 每题分值。键同时也是「群当前难度」的取值集合。
TIER_POINTS: dict[int, int] = {
    800: 100,
    1000: 150,
    1200: 220,
    1400: 320,
    1600: 450,
    1800: 620,
    2000: 850,
    2200: 1150,
    2400: 1500,
    2600: 1950,
    2800: 2500,
    3000: 3100,
    3200: 3700,
    3400: 4000,
}

TIERS: tuple[int, ...] = tuple(sorted(TIER_POINTS))
LOWEST_TIER = TIERS[0]
HIGHEST_TIER = TIERS[-1]
RATING_CEIL = 3500
DEFAULT_TIER = 1200

# 每个档位派满多少道题之后评估一次升降
ROUNDS_PER_TIER = 10

# 升档门槛：平均每人解出的题数占本轮总题数的比例
PROMOTE_RATE = 0.30
# 降档门槛
DEMOTE_RATE = 0.10


def tier_of(cf_rating: int | None, default: int = DEFAULT_TIER) -> int:
    """把 CF rating 归到档位上。

    取「不超过该 rating 的最大档位」，超出范围就夹到两个端点。
    rating 为空（CF 有极少数题没标 rating）时返回 default。
    """
    if cf_rating is None:
        return default
    rating = int(cf_rating)
    if rating <= LOWEST_TIER:
        return LOWEST_TIER
    if rating >= HIGHEST_TIER:
        return HIGHEST_TIER
    tier = LOWEST_TIER
    for candidate in TIERS:
        if candidate <= rating:
            tier = candidate
        else:
            break
    return tier


def points_for(tier: int) -> int:
    """档位对应的每题分值。传入非标准档位时先归一到最近的档位。"""
    return TIER_POINTS[tier_of(tier)]


def tier_bounds(tier: int) -> tuple[int, int]:
    """档位覆盖的 rating 闭区间（与 pick() 的 tier_of 归档一致）。"""
    low = tier_of(tier)
    high = RATING_CEIL if low == HIGHEST_TIER else min(low + 199, RATING_CEIL)
    return low, high


def tier_index(tier: int) -> int:
    return TIERS.index(tier_of(tier))


def shift_tier(tier: int, steps: int) -> int:
    """把档位上下移动若干档，自动夹在范围内。"""
    index = tier_index(tier) + int(steps)
    return TIERS[max(0, min(len(TIERS) - 1, index))]


def next_tier(current_tier: int, solved: int, issued: int) -> tuple[int, str]:
    """一轮（ROUNDS_PER_TIER 道题）结束后，算出下一轮该用哪个档位。

    solved: 这一轮里被解出的题数
    issued: 这一轮派出的题数

    注意：因为是「首个 AC 就关题」，一道题最多只有一个解出记录，
    所以 solved / issued 天然落在 0~1 之间，不需要再除以人数。

    返回 (新档位, 原因)。原因用于日志和 status 输出。
    """
    current_tier = tier_of(current_tier)
    if issued <= 0:
        return current_tier, "没有应答记录，档位不变"

    rate = solved / issued
    if rate >= PROMOTE_RATE:
        new_tier = shift_tier(current_tier, 1)
        if new_tier == current_tier:
            return current_tier, f"解出率 {rate:.0%}，已在最高档"
        return new_tier, f"解出率 {rate:.0%} ≥ {PROMOTE_RATE:.0%}，升档"
    if rate <= DEMOTE_RATE:
        new_tier = shift_tier(current_tier, -1)
        if new_tier == current_tier:
            return current_tier, f"解出率 {rate:.0%}，已在最低档"
        return new_tier, f"解出率 {rate:.0%} ≤ {DEMOTE_RATE:.0%}，降档"
    return current_tier, f"解出率 {rate:.0%}，档位不变"


def format_tier(tier: int) -> str:
    """给玩家看的档位说明。"""
    return f"CF {tier_of(tier)}（每题 {points_for(tier)} 分）"
