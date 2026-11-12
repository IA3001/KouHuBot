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

    async def test_leaderboard_marks_ties_with_the_same_rank(self):
        self.bot._store.ensure_player(self.scope, '1002', 'Bob')
        for user in ('1001', '1002'):
            self.bot._store.add_score(self.scope, user, 100, True)
        rows = self.bot._store.leaderboard(self.scope, 10)
        self.assertEqual([row['rank_label'] for row in rows], ['1', '1'])


if __name__ == '__main__':
    unittest.main()
