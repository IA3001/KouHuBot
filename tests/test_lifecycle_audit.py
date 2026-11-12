"""Scoring and round-lifecycle regressions found by the lifecycle audit."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

import _astrbot_stub as stub


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        context = type('Context', (), {})()
        context.llm_generate = AsyncMock()
        self.bot = self.mod.KouHuOJ(context, {'judge_provider_id': 'text', 'judge_ack_delay_seconds': 0})
        root = Path(self.tmp.name)
        self.bot._store = self.mod.OjStore(root / 'score.db')
        self.bot._cache = self.mod.CfCache(root / 'cache.db')
        self.bot._cf = object()
        self.scope = 'group:100200300'
        self.problem = {'cf_key': '4A', 'contest_id': 4, 'idx': 'A', 'tier': 800, 'points': 100,
                        'statement_en': 'Find max', 'statement_cn': '求最大值'}
        self.bot._cache.save(self.problem)
        self.bot._store.ensure_player(self.scope, '1001', 'Alice')
        self.bot._judge = AsyncMock(return_value={'verdict': 'AC', 'comment': '思路正确'})

    async def asyncTearDown(self):
        self.bot._store.close()
        self.bot._cache.close()
        self.tmp.cleanup()

    async def test_pitfall_draw_never_waits_for_growth(self):
        self.bot._pitfalls = object()
        self.bot._growth = type('Growth', (), {'state': {'last_attempt': 0}})()
        self.bot._grow_pitfalls = AsyncMock(side_effect=AssertionError('draw must not grow'))
        async def draw(event, arg):
            yield event.plain_result('已从题库抽题')
        self.bot._grammar_new = draw
        event = stub.AstrMessageEvent('grm new', group_id='100200300', sender_id='1001')
        out = '\n'.join([item.text async for item in self.bot.cmd_pitfall(event)])
        self.assertEqual(out, '已从题库抽题')
        self.bot._grow_pitfalls.assert_not_awaited()

    async def test_growth_status_aliases_and_help(self):
        self.bot._pitfalls = object()
        self.bot._growth = type('Growth', (), {'status': lambda self, interval=21600: '已入库：1 题'})()
        for command in ('grm status', 'grm growth', 'grm 状态'):
            event = stub.AstrMessageEvent(command, group_id='100200300', sender_id='1001')
            out = '\n'.join([item.text async for item in self.bot.cmd_pitfall(event)])
            self.assertEqual(out, '已入库：1 题')
        for command in ('grm', 'grm help', 'grm unknown'):
            event = stub.AstrMessageEvent(command, group_id='100200300', sender_id='1001')
            out = '\n'.join([item.text async for item in self.bot.cmd_pitfall(event)])
            self.assertIn('grm status', out)
            self.assertIn('grm grow ', out)
            self.assertIn('冷却', out)

    def _open(self):
        self.bot._store.open_problem(self.scope, self.problem, '1001')
        self.bot._store.note_issued(self.scope)

    async def submit(self, text='sub 先排序再取最大'):
        event = stub.AstrMessageEvent(text, group_id='100200300', sender_id='1001', sender_name='Alice')
        return '\n'.join([item.text async for item in self.bot.cmd_submit(event)])

    async def test_duplicate_ac_does_not_inflate_group_tier_counter(self):
        self._open()
        first = await self.submit()
        self.assertIn('AC', first)
        self.assertEqual(self.bot._store.state(self.scope)['tier_solved'], 1)

        # 同一道题被重新派出，同一个玩家再 AC 一次：不加分，也不能再算一次群解出。
        self._open()
        second = await self.submit('sub 又做了一遍')
        self.assertIn('已经拿过分', second)
        self.assertEqual(self.bot._store.state(self.scope)['tier_solved'], 1)
        self.assertEqual(self.bot._store.leaderboard(self.scope, 10)[0]['total'], 100)

    async def test_duplicate_ac_still_triggers_tier_evaluation(self):
        self.bot._store.set_tier(self.scope, 1200)
        for _ in range(self.mod.scoring.ROUNDS_PER_TIER - 1):
            self.bot._store.note_issued(self.scope)
        self.bot._store.note_closed(self.scope)
        self._open()
        for _ in range(self.mod.scoring.ROUNDS_PER_TIER):
            self.bot._store.mark_solved(self.scope, '1001', '4A', 0)
        self.bot._store.note_closed(self.scope)
        self.assertEqual(self.bot._store.state(self.scope)['tier_rounds'], 1)

    async def test_giveup_is_not_counted_as_a_submission(self):
        self._open()
        event = stub.AstrMessageEvent('giveup', group_id='100200300', sender_id='1001', sender_name='Alice')
        [item async for item in self.bot.cmd_giveup(event)]
        row = self.bot._store._conn.execute(
            'SELECT attempts, gave_up FROM players WHERE scope_id = ? AND user_id = ?',
            (self.scope, '1001'),
        ).fetchone()
        self.assertEqual(row['attempts'], 0)
        self.assertEqual(row['gave_up'], 1)

    async def test_late_judgement_is_recorded_even_after_round_closed(self):
        self._open()
        # 判题期间题目被别人放弃：结果仍要进 history，不能整条丢掉。
        original = self.bot._judge

        async def judge_then_close(*args, **kwargs):
            self.bot._store.close_problem(self.scope, self.mod.STATUS_GAVE_UP, '1002')
            return await original(*args, **kwargs)

        self.bot._judge = judge_then_close
        out = await self.submit()
        self.assertIn('本次不计分', out)
        self.assertIn('history', out)
        entries = self.bot._store._conn.execute('SELECT COUNT(*) FROM review_entries').fetchone()[0]
        self.assertEqual(entries, 1)

    async def test_late_judgement_stays_with_original_problem(self):
        self._open()
        original = self.bot._judge
        async def judge_then_replace(*args, **kwargs):
            self.bot._store.close_problem(self.scope, self.mod.STATUS_GAVE_UP, '1002')
            other = {**self.problem, 'cf_key': '5A', 'contest_id': 5}
            self.bot._store.open_problem(self.scope, other, '1002')
            return await original(*args, **kwargs)
        self.bot._judge = judge_then_replace
        out = await self.submit('sub 旧题的分析')
        self.assertIn('本次不计分', out)
        rows, total = self.bot._store.review_problem_entries(self.scope, '1001', '4A', 1)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['submission'], '旧题的分析')
        _, new_total = self.bot._store.review_problem_entries(self.scope, '1001', '5A', 1)
        self.assertEqual(new_total, 0)

    async def test_leaderboard_marks_ties_with_the_same_rank(self):
        self.bot._store.ensure_player(self.scope, '1002', 'Bob')
        for user in ('1001', '1002'):
            self.bot._store.add_score(self.scope, user, 100, True)
        rows = self.bot._store.leaderboard(self.scope, 10)
        self.assertEqual([row['rank_label'] for row in rows], ['1', '1'])


if __name__ == '__main__':
    unittest.main()


class GrowthLoopTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.bot = self.mod.KouHuOJ(type('Context', (), {})(), {'judge_provider_id': 'mock'})

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_background_loop_grows_when_due(self):
        calls = []

        async def fake_sleep(_seconds):
            if calls:
                raise __import__('asyncio').CancelledError()
            await __import__('asyncio').sleep(0)

        async def fake_grow(event, *, quiet=False):
            calls.append(quiet)
            return ''

        self.bot._sleep = fake_sleep
        self.bot._grow_pitfalls = fake_grow
        self.bot._growth = type('G', (), {'state': {'last_attempt': 0}, 'save': lambda self: None, 'cooldown_remaining': lambda self, interval: 0})()
        self.bot._pitfalls = type('P', (), {'count': lambda self: 1})()
        with self.assertRaises(__import__('asyncio').CancelledError):
            await self.bot._growth_loop()
        self.assertEqual(calls, [True])

    async def test_background_loop_respects_auto_growth_switch(self):
        calls = []
        ticks = []

        async def fake_sleep(_seconds):
            ticks.append(1)
            if len(ticks) > 2:
                raise __import__('asyncio').CancelledError()
            await __import__('asyncio').sleep(0)

        async def fake_grow(event, *, quiet=False):
            calls.append(quiet)
            return ''

        self.bot.config['grm_auto_growth'] = False
        self.bot._sleep = fake_sleep
        self.bot._grow_pitfalls = fake_grow
        self.bot._growth = type('G', (), {'state': {'last_attempt': 0}, 'save': lambda self: None, 'cooldown_remaining': lambda self, interval: 0})()
        self.bot._pitfalls = type('P', (), {'count': lambda self: 1})()
        with self.assertRaises(__import__('asyncio').CancelledError):
            await self.bot._growth_loop()
        self.assertEqual(calls, [])


class BackgroundProviderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_background_provider_lookup_never_raises_without_a_event(self):
        ctx = type('Context', (), {})()
        async def get_current(umo=None):
            return 'session-model'
        ctx.get_current_chat_provider_id = get_current
        bot = self.mod.KouHuOJ(ctx, {})
        # 没有配置模型、也从没收到过消息：直接放弃本次，而不是抛异常。
        self.assertIsNone(await bot._provider_id(None))
        # 收到过消息之后，后台任务沿用该会话的模型。
        bot._scope_of(stub.AstrMessageEvent('new', group_id='1', sender_id='1'))
        self.assertEqual(await bot._provider_id(None), 'session-model')

    async def test_configured_judge_model_is_used_without_any_event(self):
        ctx = type('Context', (), {})()
        bot = self.mod.KouHuOJ(ctx, {'judge_provider_id': 'judge-model'})
        self.assertEqual(await bot._provider_id(None), 'judge-model')

    async def test_falls_back_to_global_default_provider(self):
        ctx = type('Context', (), {})()
        async def no_session(umo=None):
            raise RuntimeError('no session provider')
        ctx.get_current_chat_provider_id = no_session
        provider = type('Provider', (), {'meta': lambda self: type('Meta', (), {'id': 'global-model'})()})()
        ctx.get_using_provider = lambda umo=None: provider
        bot = self.mod.KouHuOJ(ctx, {})
        bot._scope_of(stub.AstrMessageEvent('new', group_id='1', sender_id='1'))
        self.assertEqual(await bot._provider_id(None), 'global-model')
