import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'cf_tag_parser', Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_kouhu_oj/cf_tags.py'
)
tags = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tags)

AVAILABLE = [
    '2-sat', 'binary search', 'bitmasks', 'brute force', 'chinese remainder theorem',
    'combinatorics', 'communication', 'constructive algorithms', 'data structures',
    'dfs and similar', 'divide and conquer', 'dp', 'dsu', 'expression parsing', 'fft',
    'flows', 'games', 'geometry', 'graph matchings', 'graphs', 'greedy', 'hashing',
    'implementation', 'interactive', 'math', 'matrices', 'meet-in-the-middle',
    'number theory', 'probabilities', 'schedules', 'shortest paths', 'sortings',
    'string suffix structures', 'strings', 'ternary search', 'trees', 'two pointers',
    'future new tag',
]


class TagParserTest(unittest.TestCase):
    def parse(self, text):
        return tags.parse_tag_query(text, AVAILABLE)

    def assert_tags(self, text, expected, bounds=None):
        got_tags, got_bounds, _ = self.parse(text)
        self.assertEqual(got_tags, expected, text)
        self.assertEqual(got_bounds, bounds, text)

    def test_every_live_tag_and_case(self):
        for tag in AVAILABLE:
            self.assert_tags(tag, [tag])
            self.assert_tags(tag.upper(), [tag])

    def test_singular_hyphen_and_abbreviations(self):
        cases = {
            'graph': 'graphs', 'tree': 'trees', 'string': 'strings',
            'sorting': 'sortings', 'flow': 'flows', 'game': 'games',
            'schedule': 'schedules', 'bitmask': 'bitmasks',
            'probability': 'probabilities', 'matrix': 'matrices',
            'shortest path': 'shortest paths', 'two pointer': 'two pointers',
            'data structure': 'data structures',
            'constructive algorithm': 'constructive algorithms',
            'graph matching': 'graph matchings',
            'string suffix structure': 'string suffix structures',
            'dfs': 'dfs and similar', '2SAT': '2-sat', '2 sat': '2-sat',
            'binary-search': 'binary search', 'data-structures': 'data structures',
            'shortest-path': 'shortest paths', 'two-pointers': 'two pointers',
            'divide-and-conquer': 'divide and conquer',
            'meet in the middle': 'meet-in-the-middle',
            'binarysearch': 'binary search',
        }
        for query, canonical in cases.items():
            self.assert_tags(query, [canonical])

    def test_chinese_aliases_and_space_separators(self):
        self.assert_tags('动态规划', ['dp'])
        self.assert_tags('图论', ['graphs'])
        self.assert_tags('线段树', ['data structures'])
        self.assert_tags('树形dp', ['dp', 'trees'])
        self.assert_tags('状压dp', ['dp', 'bitmasks'])
        self.assert_tags('折半搜索', ['meet-in-the-middle'])
        self.assert_tags('通信', ['communication'])
        self.assert_tags('dp trees', ['dp', 'trees'])
        self.assert_tags('图论 树', ['graphs', 'trees'])
        self.assert_tags('dp、trees', ['dp', 'trees'])
        self.assert_tags('dp；trees', ['dp', 'trees'])
        self.assert_tags('dp/trees', ['dp', 'trees'])
        self.assert_tags('dp，trees', ['dp', 'trees'])
        self.assert_tags('dp＋trees', ['dp', 'trees'])
        self.assert_tags('dp　trees', ['dp', 'trees'])
        self.assert_tags('dp。', ['dp'])
        self.assert_tags('dp?', ['dp'])
        self.assert_tags('ｄｐ', ['dp'])

    def test_div_and_rating_variants(self):
        self.assert_tags('dp 2', ['dp'], (1400, 2100))
        self.assert_tags('dp2', ['dp'], (1400, 2100))
        self.assert_tags('图论2', ['graphs'], (1400, 2100))
        for text in ('dp div2', 'dp div 2', 'dp D2', 'dp 难度2'):
            self.assert_tags(text, ['dp'], (1400, 2100))
        for text in ('dp 1200-1600', 'dp 1200–1600', 'dp 1200—1600', 'dp 1200~1600',
                     'dp 1200到1600', 'dp 1200至1600', 'dp 1200－1600', 'dp 1200 - 1600',
                     'dp[1200-1600]', 'dp（1200-1600）', 'dp［1200-1600］', 'dp(1200-1600)',
                     'dp 1200-1600分', 'dp rating 1200-1600', 'dp r 1200-1600',
                     'dp 1200-1600 2', 'dp 2 1200-1600'):
            self.assert_tags(text, ['dp'], (1200, 1600))
        self.assert_tags('动态规划+树 2', ['dp', 'trees'], (1400, 2100))
        self.assert_tags('dp++trees', ['dp', 'trees'])
        self.assert_tags('dp+', ['dp'])

    def test_unknown_bad_range_and_suggestions(self):
        for query in ('unknown', 'dp 1600-1200', 'dp 500-1000', 'dp 3500-3600',
                      'dp 5', 'dp div5', '1200-1600', 'matrixx'):
            with self.assertRaises(ValueError, msg=query):
                self.parse(query)
        with self.assertRaises(tags.TagQueryError) as ctx:
            self.parse('treee')
        self.assertIn('trees', str(ctx.exception))
        with self.assertRaises(tags.TagQueryError) as ctx:
            self.parse('dp 2 3')
        self.assertIn('只能指定一个', str(ctx.exception))

    def test_aliases_count(self):
        self.assertIn('动态规划', tags.chinese_aliases('dp'))
        self.assertNotIn('线段树', tags.chinese_aliases('data structures'))


if __name__ == '__main__':
    unittest.main()
