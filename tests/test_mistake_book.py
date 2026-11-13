"""错题本（mistake_book）查询口径、分页与渲染的回归测试。

测试不依赖 main.py 是否已经接线：直接拿插件真实的 OjStore（已含 review 表）
并把 MistakeBookMixin.mistakes 绑上去；接线之后这个绑定会被自动跳过。
"""

import tempfile
import time
import types
from pathlib import Path
import unittest

import _astrbot_stub as stub

DAY = 86400.0
ROUND_OPENED = 1000.0  # 轮次的 opened_at，同一道题的同一轮共用


class MistakeBookTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls._tmp.name))
        # load_plugin 已把插件目录塞进 sys.path，和 test_text_budget 一样
        cls.book = __import__('mistake_book')

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = self.mod.OjStore(Path(self.dir.name) / 'store.db')
        if not hasattr(self.store, 'mistakes'):
            self.store.mistakes = types.MethodType(
                self.book.MistakeBookMixin.mistakes, self.store)
        self.scope = 'group:100200300'
        self.now = time.time()

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    # -- 造数据 ------------------------------------------------------
    def add(self, key, verdict, at, *, user='alice', scope=None, title='例题',
            comment='判词内容', status='ac', opened=ROUND_OPENED):
        """直接写库：测试需要精确控制 opened_at / at，绕过 note_review 的当前时间。"""
        scope = scope or self.scope
        round_id = self.store.ensure_round(
            scope, {'cf_key': key, 'title': title, 'opened_at': opened, 'status': status})
        self.store._conn.execute(
            'INSERT INTO review_entries'
            '(round_id,user_id,submission,verdict,comment,points,hint_level,at)'
            ' VALUES(?,?,?,?,?,0,0,?)',
            (round_id, user, '作答', verdict, comment, at))
        self.store._conn.commit()
        return round_id

    def keys(self, items):
        return [item['problem_key'] for item in items]

    def texts(self, items, **kwargs):
        return '\n'.join(self.book.mistakes_text(items, **kwargs))


class MistakeQueryTest(MistakeBookTestBase):
    def test_wa_enters_book_and_ac_removes_it(self):
        self.add('628B', 'WA', self.now - 2 * DAY, title='Two Sum', comment='边界没考虑 n=1')
        items, total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(total, 1)
        self.assertEqual(len(items), 1)
        item = items[0]
        # 返回字段固定，typo 会在接线时立刻暴露
        self.assertEqual(
            set(item),
            {'problem_key', 'cf_key', 'title', 'fail_count', 'last_at', 'last_verdict',
             'last_comment', 'first_at', 'days_since', 'due'})
        self.assertEqual(item['problem_key'], '628B')
        self.assertEqual(item['cf_key'], '628B')  # 老命名兼容
        self.assertEqual(item['title'], 'Two Sum')
        self.assertEqual(item['fail_count'], 1)
        self.assertEqual(item['last_verdict'], 'WA')
        self.assertEqual(item['last_comment'], '边界没考虑 n=1')
        self.assertAlmostEqual(item['days_since'], 2.0, delta=0.01)
        self.assertEqual(item['last_at'], item['first_at'])
        self.assertFalse(item['due'])  # 2 天 < 默认 3 天
        # 再答对一次就该移出错题本
        self.add('628B', 'AC', self.now - DAY)
        items, total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual((items, total), ([], 0))

    def test_ac_in_an_older_round_still_removes_the_problem(self):
        self.add('4A', 'AC', self.now - 5 * DAY, opened=ROUND_OPENED)
        self.add('4A', 'WA', self.now - 2 * DAY, opened=ROUND_OPENED + 10)
        self.assertEqual(self.store.mistakes(self.scope, 'alice'), ([], 0))
        # 只按 scope + user 过滤：别人的 AC 不算自己的
        self.add('5A', 'WA', self.now - 2 * DAY, user='bob')
        self.add('5A', 'AC', self.now - DAY, user='bob')
        self.assertEqual(self.store.mistakes(self.scope, 'alice'), ([], 0))

    def test_open_round_hides_the_problem_until_it_ends(self):
        self.add('1000A', 'WA', self.now - 2 * DAY, status='open')
        self.assertEqual(self.store.mistakes(self.scope, 'alice'), ([], 0))
        # 同题结束（状态回写同一轮）之后才出现
        self.store.ensure_round(self.scope, {
            'cf_key': '1000A', 'title': '例题', 'opened_at': ROUND_OPENED, 'status': 'gave_up'})
        items, total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(total, 1)
        self.assertEqual(self.keys(items), ['1000A'])

    def test_newer_open_round_hides_even_if_older_round_closed(self):
        self.add('1000A', 'WA', self.now - 2 * DAY, opened=ROUND_OPENED)
        # 同一道题重开了一轮，还没结束 -> 整个题号都不该露出
        self.add('1000B', 'WA', self.now - 3 * DAY, opened=ROUND_OPENED, comment='ok')
        self.store.ensure_round(self.scope, {
            'cf_key': '1000A', 'title': '例题', 'opened_at': ROUND_OPENED + 50, 'status': 'open'})
        items, total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(self.keys(items), ['1000B'])

    def test_due_filter_and_ordering_put_the_oldest_first(self):
        self.add('1000A', 'WA', self.now - 10 * DAY)
        self.add('1000B', 'WA', self.now - 5 * DAY)
        self.add('1000C', 'WA', self.now - 0.05 * DAY)
        items, total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(total, 3)
        self.assertEqual(self.keys(items), ['1000A', '1000B', '1000C'])
        self.assertEqual([item['due'] for item in items], [True, True, False])
        due_items, due_total = self.store.mistakes(self.scope, 'alice', due_only=True)
        self.assertEqual(due_total, 2)
        self.assertEqual(self.keys(due_items), ['1000A', '1000B'])
        # 阈值可调：0 天时刚刚作答的也算到期
        everything, all_total = self.store.mistakes(
            self.scope, 'alice', due_only=True, due_days=0)
        self.assertEqual(all_total, 3)
        self.assertEqual(self.keys(everything), ['1000A', '1000B', '1000C'])

    def test_pagination_clamps_out_of_range_pages(self):
        for n in range(7):  # 越早作答的排越前
            self.add(f'100{n}A', 'WA', self.now - (20 - n) * DAY)
        first, total = self.store.mistakes(self.scope, 'alice', page=1, page_size=5)
        self.assertEqual(total, 7)
        self.assertEqual(len(first), 5)
        self.assertEqual(self.keys(first)[0], '1000A')
        second, _ = self.store.mistakes(self.scope, 'alice', page=2, page_size=5)
        self.assertEqual(self.keys(second), ['1005A', '1006A'])
        # 越界页码夹到最后一页，越界页大小夹到 1，非法值回落默认值，都不抛异常
        clamped, _ = self.store.mistakes(self.scope, 'alice', page=99, page_size=5)
        self.assertEqual(self.keys(clamped), ['1005A', '1006A'])
        zero, _ = self.store.mistakes(self.scope, 'alice', page=0, page_size=5)
        self.assertEqual(len(zero), 5)
        negative, _ = self.store.mistakes(self.scope, 'alice', page=-3, page_size=-1)
        self.assertEqual(len(negative), 1)
        self.assertEqual(self.keys(negative), ['1000A'])
        junk, junk_total = self.store.mistakes(self.scope, 'alice', page='x', page_size='y')
        self.assertEqual((len(junk), junk_total), (5, 7))

    def test_user_and_scope_are_isolated(self):
        self.add('1000A', 'WA', self.now - 2 * DAY, user='alice')
        self.add('1000B', 'WA', self.now - 2 * DAY, user='bob')
        self.add('1000C', 'WA', self.now - 2 * DAY, user='alice', scope='group:other')
        self.assertEqual(self.keys(self.store.mistakes(self.scope, 'alice')[0]), ['1000A'])
        self.assertEqual(self.keys(self.store.mistakes(self.scope, 'bob')[0]), ['1000B'])
        self.assertEqual(
            self.keys(self.store.mistakes('group:other', 'alice')[0]), ['1000C'])
        self.assertEqual(self.store.mistakes(self.scope, 'carol'), ([], 0))

    def test_grammar_lane_is_a_separate_book(self):
        grammar_scope = self.scope + self.mod.GRAMMAR_SUFFIX
        self.add('1000A', 'WA', self.now - 2 * DAY, user='alice')
        self.add(self.mod.PITFALL_PREFIX + 'fp-1', 'WA', self.now - 4 * DAY,
                 user='alice', scope=grammar_scope, title='浮点数比较')
        self.assertEqual(self.keys(self.store.mistakes(self.scope, 'alice')[0]), ['1000A'])
        grm_items, grm_total = self.store.mistakes(grammar_scope, 'alice')
        self.assertEqual(grm_total, 1)
        self.assertEqual(self.keys(grm_items), [self.mod.PITFALL_PREFIX + 'fp-1'])
        self.assertTrue(grm_items[0]['due'])
        # 正题线路里看不到坑点题，反之亦然
        self.assertNotIn(self.mod.PITFALL_PREFIX + 'fp-1',
                         self.keys(self.store.mistakes(self.scope, 'alice')[0]))
        self.assertNotIn('1000A', self.keys(grm_items))

    def test_title_falls_back_when_the_newest_round_has_none(self):
        # 只答过老一轮；最新一轮（重开）没有标题 -> 用作答那一轮的标题
        self.add('628B', 'WA', self.now - 3 * DAY, title='旧标题', opened=ROUND_OPENED)
        self.store.ensure_round(self.scope, {
            'cf_key': '628B', 'title': None, 'opened_at': ROUND_OPENED + 50, 'status': 'ac'})
        items, total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(total, 1)
        self.assertEqual(items[0]['title'], '旧标题')
        # 两轮都没有标题时退回到题号，不能给出空标题
        self.store.ensure_round(self.scope, {
            'cf_key': '628B', 'title': '', 'opened_at': ROUND_OPENED, 'status': 'ac'})
        items, _ = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(items[0]['title'], '628B')

    def test_multi_round_problem_appears_once_with_full_fail_count(self):
        self.add('628B', 'WA', self.now - 5 * DAY, title='旧标题', opened=ROUND_OPENED)
        self.add('628B', 'WA', self.now - DAY, title='新标题', opened=ROUND_OPENED + 50,
                 comment='还是不对')
        self.add('628B', 'WA', self.now - 2 * 3600.0, title='新标题',
                 opened=ROUND_OPENED + 50, comment='最近这次判词')
        items, total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(total, 1)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item['fail_count'], 3)
        self.assertAlmostEqual(item['first_at'], self.now - 5 * DAY, delta=0.01)
        self.assertAlmostEqual(item['last_at'], self.now - 2 * 3600.0, delta=0.01)
        self.assertEqual(item['title'], '新标题')  # 取最新一轮的标题
        self.assertEqual(item['last_comment'], '最近这次判词')  # 取最新一条判词
        self.assertLess(item['days_since'], 0.2)


class MistakeTextTest(MistakeBookTestBase):
    def test_empty_book_and_empty_due_are_worded_differently(self):
        items, total = self.store.mistakes(self.scope, 'alice')
        text = '\n'.join(self.book.mistakes_text(items, page=1, pages=1, total=total))
        self.assertIn('目前没有待复习的错题', text)
        self.assertNotIn('还没到复习时间', text)
        self.assertIn('mistakes', text)
        self.assertEqual(
            len(self.book.mistakes_text([], page=1, pages=1, total=0)), 1)

        self.add('1000A', 'WA', self.now - 3600.0)
        items, total = self.store.mistakes(self.scope, 'alice', due_only=True)
        due_text = '\n'.join(self.book.mistakes_text(
            items, page=1, pages=1, total=total, due_only=True))
        self.assertIn('还没到复习时间', due_text)
        self.assertNotIn('目前没有待复习的错题', due_text)
        self.assertIn('mistakes', due_text)
        # 全部错题里能看到这条，只是没到期
        every, every_total = self.store.mistakes(self.scope, 'alice')
        self.assertEqual(every_total, 1)

    def test_items_and_pagination_hints_are_plain_text(self):
        self.add('628B', 'WA', self.now - 5 * DAY, comment='思路跑偏，边界没考虑 n=1')
        self.add('1000A', 'GIVEUP', self.now - 4 * DAY, comment='看了解析')
        items, total = self.store.mistakes(self.scope, 'alice')
        first = self.texts(items, page=1, pages=2, total=6)
        self.assertIn('628B', first)
        self.assertIn('未过 1 次', first)
        self.assertIn('距今 5 天', first)
        self.assertIn('该复习了', first)
        self.assertIn('WA', first)
        self.assertIn('思路跑偏，边界没考虑 n=1', first)
        self.assertIn('重做：new CF628B｜回看：history show 628B', first)
        self.assertIn('重做：new CF1000A｜回看：history show 1000A', first)
        self.assertIn('下一页：mistakes 2', first)
        self.assertNotIn('上一页', first)
        second = self.texts(items, page=2, pages=2, total=6)
        self.assertIn('上一页：mistakes 1', second)
        self.assertNotIn('下一页', second)
        single = self.texts(items, page=1, pages=1, total=2)
        self.assertIn('mistakes [页码]', single)
        for marker in ('**', '`', '##', '![', '](', '- '):
            self.assertNotIn(marker, first)
            self.assertNotIn(marker, single)

    def test_long_comment_is_clipped_and_volume_stays_small(self):
        self.add('628B', 'WA', self.now - 5 * DAY, comment='判词内容' * 100)
        items, total = self.store.mistakes(self.scope, 'alice')
        text = self.texts(items, page=1, pages=1, total=total)
        self.assertIn('…', text)
        for line in text.splitlines():
            if '判词：' in line:
                self.assertLessEqual(len(line), 80)
        # 每页 5 条时的整体预算
        five = []
        for n in range(5):
            five.append({
                'problem_key': f'100{n}A', 'cf_key': f'100{n}A', 'title': '题',
                'fail_count': 3, 'last_at': self.now - (5 + n) * DAY,
                'last_verdict': 'WA',
                'last_comment': '这段判词写得比较长，用来占一点体积，确保预算不会被撑爆。',
                'first_at': self.now - 9 * DAY, 'days_since': 5.0 + n, 'due': True,
            })
        chunks = self.book.mistakes_text(five, page=1, pages=1, total=5)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertLessEqual(sum(len(chunk) for chunk in chunks), 1200)

    def test_grammar_items_point_at_the_grammar_commands(self):
        grammar_scope = self.scope + self.mod.GRAMMAR_SUFFIX
        key = self.mod.PITFALL_PREFIX + 'fp-1'
        self.add(key, 'WA', self.now - 4 * DAY, scope=grammar_scope, title='浮点数比较')
        items, total = self.store.mistakes(grammar_scope, 'alice')
        text = self.texts(items, page=1, pages=1, total=total)
        self.assertIn('重做：grm new', text)
        self.assertIn('回看：history grm show fp-1', text)
        self.assertNotIn('new CFgrm:', text)
        self.assertIn('距今 4 天', text)

    def test_hour_scale_distance_uses_hours(self):
        self.add('628B', 'WA', self.now - 3 * 3600.0)
        items, _ = self.store.mistakes(self.scope, 'alice')
        text = self.texts(items, page=1, pages=1, total=1)
        self.assertIn('距今 3 小时', text)


if __name__ == '__main__':
    unittest.main()
