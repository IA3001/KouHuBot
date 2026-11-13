"""mistakes 指令：错题本的口径、参数容忍与群聊渲染。"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import _astrbot_stub as stub


class MistakesCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.bot = self.mod.KouHuOJ(
            SimpleNamespace(llm_generate=AsyncMock()),
            {'judge_provider_id': 'text', 'new_cooldown_seconds': 0},
        )
        root = Path(self.tmp.name)
        self.bot._store = self.mod.OjStore(root / 'score.db')
        self.bot._cache = self.mod.CfCache(root / 'cache.db')
        self.scope = 'group:100200300'
        self.bot._store.ensure_player(self.scope, '1001', 'Alice')

    async def asyncTearDown(self):
        self.bot._store.close()
        self.tmp.cleanup()

    def event(self, text, sender_id='1001'):
        return stub.AstrMessageEvent(text, group_id='100200300', sender_id=sender_id)

    def round(self, key, verdict='WA', comment='思路漏了边界', at=None, close=True,
              opened_at=None):
        """造一轮「已结束」的题并写入一条作答。"""
        problem = {'cf_key': key, 'title': '匿名题目', 'points': 100, 'tier': 800}
        current = dict(problem, opened_at=opened_at or time.time(), status='open')
        self.bot._store.open_problem(self.scope, problem, '1001')
        row = self.bot._store.current(self.scope)
        self.bot._store.note_review(
            self.scope, row, '1001', '我的作答', verdict, comment)
        if close:
            self.bot._store.close_problem(
                self.scope, 'ac' if verdict == 'AC' else 'gave_up', '1001')
        return row

    async def run_cmd(self, text):
        return [item.text async for item in self.bot.cmd_mistakes(self.event(text))]

    async def test_wa_problem_enters_and_ac_removes_it(self):
        self.round('628B', 'WA', '没有处理 n 为偶数的情况')
        out = (await self.run_cmd('mistakes'))[0]
        self.assertIn('628B', out)
        self.assertIn('未过 1 次', out)
        self.assertIn('new CF628B', out)
        self.assertIn('history show 628B', out)
        self.round('628B', 'AC', '对了')
        out = (await self.run_cmd('mistakes'))[0]
        self.assertIn('没有待复习的错题', out)
        self.assertNotIn('628B', out)

    async def test_in_progress_problem_is_hidden(self):
        self.round('700C', 'WA', close=False)
        out = (await self.run_cmd('mistakes'))[0]
        self.assertNotIn('700C', out)
        self.assertIn('没有待复习的错题', out)

    async def test_due_filter_and_oldest_first(self):
        old = self.round('800A', 'WA')
        self.bot._store._conn.execute(
            "UPDATE review_entries SET at = ?", (time.time() - 9 * 86400,))
        self.bot._store._conn.commit()
        self.round('801B', 'WA')
        everything = (await self.run_cmd('mistakes'))[0]
        self.assertLess(everything.index('800A'), everything.index('801B'))
        self.assertIn('该复习了', everything)
        due = (await self.run_cmd('mistakes due'))[0]
        self.assertIn('800A', due)
        self.assertNotIn('801B', due)
        self.assertIn('错题本 · 待复习', due)

    async def test_due_days_is_configurable(self):
        self.round('802C', 'WA')
        self.bot.config['mistake_due_days'] = 0
        out = (await self.run_cmd('mistakes due'))[0]
        self.assertIn('802C', out)

    async def test_paging_and_clamped_page(self):
        for index, key in enumerate(('900A', '900B', '900C', '900D', '900E', '900F')):
            self.round(key, 'WA')
        first = (await self.run_cmd('mistakes'))[0]
        self.assertIn('1/2 页', first)
        self.assertIn('共 6 题', first)
        self.assertIn('下一页：mistakes 2', first)
        second = (await self.run_cmd('mistakes 2'))[0]
        self.assertIn('2/2 页', second)
        self.assertIn('上一页：mistakes 1', second)
        # 页码越界夹到最后一页，不报错也不空白
        clamped = (await self.run_cmd('mistakes 9'))[0]
        self.assertIn('2/2 页', clamped)

    async def test_argument_order_is_tolerant(self):
        self.round('901A', 'WA')
        for text in ('mistakes 1', '错题本', '复习', 'Mistakes!'):
            out = (await self.run_cmd(text))[0]
            self.assertIn('901A', out, text)
        # due 和页码顺序随意；刚做过的题不算到期，所以把阈值调成 0 再验一遍
        self.bot.config['mistake_due_days'] = 0
        for text in ('mistakes 1 due', '错题本 due', '复习 到期', 'Mistakes Due!'):
            out = (await self.run_cmd(text))[0]
            self.assertIn('901A', out, text)
        # 重复页码不算错：最后一个生效，越界仍然夹到最后一页
        out = (await self.run_cmd('mistakes 1 2'))[0]
        self.assertIn('901A', out)
        self.assertIn('1/1 页', out)

    async def test_bad_argument_shows_usage_without_querying(self):
        self.bot._store.mistakes = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError('非法参数不该查库'))
        out = (await self.run_cmd('mistakes 题号'))[0]
        self.assertIn('用法：mistakes', out)

    async def test_pitfall_lane_is_separate(self):
        grammar_scope = self.scope + self.mod.GRAMMAR_SUFFIX
        self.bot._store.open_problem(
            grammar_scope, {'cf_key': self.mod.PITFALL_PREFIX + 'float-compare'}, '1001')
        row = self.bot._store.current(grammar_scope)
        self.bot._store.note_review(
            grammar_scope, row, '1001', '我看不出来', 'WA', '没抓到浮点比较的坑')
        self.bot._store.close_problem(grammar_scope, 'gave_up', '1001')
        self.round('902B', 'WA')
        plain = (await self.run_cmd('mistakes'))[0]
        self.assertNotIn('float-compare', plain)
        gram = (await self.run_cmd('mistakes grm'))[0]
        self.assertIn('float-compare', gram)
        self.assertIn('grm new', gram)
        self.assertNotIn('902B', gram)

    async def test_other_user_and_scope_are_invisible(self):
        self.round('903C', 'WA')
        self.bot._store.ensure_player('group:999', '2002', 'Bob')
        self.bot._store.open_problem(
            'group:999', {'cf_key': '904D', 'title': '别人的题', 'points': 100}, '2002')
        row = self.bot._store.current('group:999')
        self.bot._store.note_review('group:999', row, '2002', 'x', 'WA', '别人的错题')
        self.bot._store.close_problem('group:999', 'gave_up', '2002')
        other_user = [item.text async for item in
                      self.bot.cmd_mistakes(self.event('mistakes', sender_id='1003'))]
        self.assertNotIn('903C', other_user[0])
        self.assertNotIn('904D', other_user[0])

    async def test_repeat_failures_count_once_per_problem(self):
        self.round('905E', 'WA', '第一次的判词')
        self.round('905E', 'GIVEUP', '放弃时的判词')
        out = (await self.run_cmd('mistakes'))[0]
        self.assertEqual(out.count('905E ·'), 1)
        self.assertIn('未过 2 次', out)
        # 最后一次是放弃：不再复读「主动放弃」这种没信息量的判词
        self.assertIn('当时直接看了答案', out)
        self.assertNotIn('放弃时的判词', out)

    async def test_empty_state_explains_how_to_fill_it(self):
        out = (await self.run_cmd('mistakes due'))[0]
        self.assertIn('没有到期', out)
        self.assertIn('mistakes', out)


if __name__ == '__main__':
    unittest.main()
