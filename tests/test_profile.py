"""个人主页口径：rating、今日/本周/本月、热力图、积分曲线、最近做题。

这些数字是群里天天看的，口径错一次就会被指出来，所以这里按「纯函数」测：
构造已知时间戳，断言算出来的天数、段位、格子、曲线全对得上。
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "astrbot_plugin_kouhu_oj"))

import profile as P           # noqa: E402
import scoring                # noqa: E402


def local(year, month, day, hour=12):
    """本地时区的时间戳（跟 main.py 显示时间用的是同一套 localtime）。"""
    return time.mktime((year, month, day, hour, 0, 0, 0, 0, -1))


def solve(key, tier, at, title="题"):
    return {"key": key, "title": title, "points": scoring.TIER_POINTS[tier], "at": at}


class TierAndRankTests(unittest.TestCase):
    def test_points_map_back_to_every_tier(self):
        for tier in scoring.TIERS:
            self.assertEqual(P.tier_of_points(scoring.TIER_POINTS[tier]), tier)

    def test_unknown_points_do_not_crash(self):
        for bad in (None, 0, 123, "abc"):
            self.assertIsNone(P.tier_of_points(bad))

    def test_rank_bands(self):
        cases = {800: "newbie", 1199: "newbie", 1200: "pupil", 1399: "pupil",
                 1400: "specialist", 1600: "expert", 1899: "expert",
                 1900: "cm", 2100: "master", 2400: "gm", 3400: "gm"}
        for rating, key in cases.items():
            with self.subTest(rating=rating):
                self.assertEqual(P.rank_of(rating)[0], key)

    def test_unrated_has_no_rank(self):
        self.assertIsNone(P.rank_of(None))


class RatingTests(unittest.TestCase):
    """rating 是「稳定解出的档位」，不是积分，也不是最高难度。"""

    def build(self, tiers):
        now = local(2026, 11, 14)
        solves = [solve("18%dA" % i, tier, now - i * 86400) for i, tier in enumerate(tiers)]
        return P.build_profile(solves, now=now)

    def test_too_few_solves_stays_unrated(self):
        for count in (0, 1, 2):
            with self.subTest(count=count):
                self.assertIsNone(self.build([1400] * count)["rating"])

    def test_three_solves_is_enough_to_rate(self):
        prof = self.build([1400, 1400, 1400])
        self.assertEqual(prof["rating"], 1400)
        self.assertEqual(prof["rank"], ("specialist", "熟练"))

    def test_rating_is_the_average_tier_rounded_to_50(self):
        self.assertEqual(self.build([1200, 1400, 1600])["rating"], 1400)
        # 3400/3 = 1133.33 → 1150（往下取整会掉到 1100，那就不是「平均档位」了）
        self.assertEqual(self.build([800, 1200, 1400])["rating"], 1150)
        self.assertEqual(self.build([1400, 1400, 1200, 1400, 1600])["rating"], 1400)

    def test_rating_is_clamped_to_the_tier_table(self):
        self.assertEqual(self.build([800] * 4)["rating"], scoring.LOWEST_TIER)
        self.assertEqual(self.build([3400] * 4)["rating"], scoring.HIGHEST_TIER)

    def test_hardest_solve_is_reported_separately(self):
        prof = self.build([800, 2200, 1000])
        self.assertEqual(prof["peak_tier"], 2200)
        self.assertEqual(prof["peak_key"], "181A")


class WindowTests(unittest.TestCase):
    """今天/本周/本月按本地日历切，周一是一周的第一天。"""

    def setUp(self):
        self.now = local(2026, 11, 14, 21)      # 2026-11-14 是周六

    def test_today_week_month_counts(self):
        solves = [
            solve("A", 800, local(2026, 11, 14, 9)),    # 今天
            solve("B", 1000, local(2026, 11, 14, 10)),  # 今天
            solve("C", 1200, local(2026, 11, 9, 20)),   # 本周一（本周）
            solve("D", 1400, local(2026, 11, 5, 20)),   # 上周（只算本月）
            solve("E", 1600, local(2026, 10, 31, 20)),  # 上月
        ]
        prof = P.build_profile(solves, now=self.now)
        self.assertEqual(prof["today"], {"count": 2, "points": 100 + 150})
        self.assertEqual(prof["week"], {"count": 3, "points": 100 + 150 + 220})
        self.assertEqual(prof["month"], {"count": 4, "points": 100 + 150 + 220 + 320})

    def test_month_starts_on_the_first(self):
        solves = [solve("A", 800, local(2026, 11, 1, 0)),
                  solve("B", 800, local(2026, 10, 31, 23))]
        prof = P.build_profile(solves, now=self.now)
        self.assertEqual(prof["month"]["count"], 1)

    def test_streaks(self):
        solves = [solve(k, 800, local(2026, 11, day))
                  for k, day in (("A", 12), ("B", 13), ("C", 14),
                                 ("D", 5), ("E", 6), ("F", 7), ("G", 8))]
        prof = P.build_profile(solves, now=self.now)
        self.assertEqual(prof["streak"], 3)          # 当前：12/13/14
        self.assertEqual(prof["longest_streak"], 4)  # 历史：5/6/7/8

    def test_streak_survives_a_day_that_has_not_started_yet(self):
        prof = P.build_profile([solve("A", 800, local(2026, 11, 13))], now=self.now)
        self.assertEqual(prof["streak"], 1)   # 今天还没做题，昨天做了也算连着

    def test_streak_resets_after_a_gap(self):
        prof = P.build_profile([solve("A", 800, local(2026, 11, 10))], now=self.now)
        self.assertEqual(prof["streak"], 0)


class HeatmapTests(unittest.TestCase):
    def setUp(self):
        self.now = local(2026, 11, 14, 21)

    def test_grid_is_full_weeks_by_seven_days(self):
        prof = P.build_profile([], now=self.now)
        cells = prof["heatmap"]["cells"]
        self.assertEqual(len(cells), P.HEATMAP_WEEKS * 7)
        self.assertEqual(cells[0]["date"][1:], (5, 18))   # 第一列是 26 周前的周一

    def test_levels_follow_daily_counts(self):
        solves = [solve("A", 800, local(2026, 11, 14, 9)),
                  solve("B", 800, local(2026, 11, 14, 10)),
                  solve("C", 800, local(2026, 11, 14, 11)),
                  solve("D", 800, local(2026, 11, 14, 12)),
                  solve("E", 800, local(2026, 11, 13, 9))]
        cells = P.build_profile(solves, now=self.now)["heatmap"]["cells"]
        by_date = {cell["date"]: cell for cell in cells}
        self.assertEqual(by_date[(2026, 11, 14)]["count"], 4)
        self.assertEqual(by_date[(2026, 11, 14)]["level"], 4)
        self.assertEqual(by_date[(2026, 11, 13)]["level"], 1)
        self.assertEqual(by_date[(2026, 11, 12)]["level"], 0)

    def test_days_after_today_are_marked_future(self):
        cells = P.build_profile([], now=self.now)["heatmap"]["cells"]
        future = [cell for cell in cells if cell["future"]]
        # 2026-11-14 是周六，所以本周只剩周日一格是未来
        self.assertEqual([cell["date"] for cell in future], [(2026, 11, 15)])

    def test_month_labels_change_on_the_first_column(self):
        months = P.build_profile([], now=self.now)["heatmap"]["months"]
        self.assertEqual(months[0][0], 0)
        self.assertTrue(all(label.endswith("月") for _, label in months))
        # 2026-05-18 起，26 周正好跨 7 个月（含起始那个月）
        self.assertEqual([label for _, label in months],
                         ["5月", "6月", "7月", "8月", "9月", "10月", "11月"])

    def test_active_days_counts_distinct_days(self):
        solves = [solve("A", 800, local(2026, 11, 14, 9)),
                  solve("B", 800, local(2026, 11, 14, 10)),
                  solve("C", 800, local(2026, 11, 2, 10))]
        self.assertEqual(P.build_profile(solves, now=self.now)["active_days"], 2)


class CurveAndRecentTests(unittest.TestCase):
    def setUp(self):
        self.now = local(2026, 11, 14, 21)

    def test_curve_is_cumulative_and_stops_today(self):
        solves = [solve("A", 800, local(2026, 11, 12)),
                  solve("B", 1200, local(2026, 11, 14))]
        prof = P.build_profile(solves, now=self.now)
        curve = prof["curve"]
        totals = [row["total"] for row in curve]
        self.assertEqual(totals, sorted(totals))              # 单调不减
        self.assertEqual(curve[-1]["day"], (2026, 11, 14))    # 只画到今天
        self.assertEqual(curve[-1]["total"], 100 + 220)

    def test_curve_carries_history_from_before_the_window(self):
        solves = [solve("old", 800, local(2026, 1, 1))]
        curve = P.build_profile(solves, now=self.now)["curve"]
        self.assertEqual(curve[0]["total"], 100)   # 窗口起点之前的分也要算进去

    def test_recent_is_newest_first_and_limited(self):
        solves = [solve("18%dA" % i, 800, local(2026, 11, 10 + i)) for i in range(4)]
        prof = P.build_profile(solves, now=self.now, recent=3)
        self.assertEqual([row["key"] for row in prof["recent"]], ["183A", "182A", "181A"])
        self.assertEqual(prof["recent"][0]["tier"], 800)
        self.assertEqual(prof["recent"][0]["points"], 100)

    def test_empty_profile_is_renderable(self):
        prof = P.build_profile([], now=self.now)
        self.assertEqual(prof["solves"], 0)
        self.assertEqual(prof["points"], 0)
        self.assertIsNone(prof["rating"])
        self.assertIsNone(prof["rank"])
        self.assertIsNone(prof["peak_tier"])
        self.assertEqual(prof["recent"], [])
        self.assertEqual(prof["today"], {"count": 0, "points": 0})


if __name__ == "__main__":
    unittest.main()
