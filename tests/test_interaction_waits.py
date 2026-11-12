"""Slow operations must acknowledge work and reject duplicate requests."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import _astrbot_stub as stub


class InteractionWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.ctx = SimpleNamespace(llm_generate=AsyncMock())
        self.bot = self.mod.KouHuOJ(self.ctx, {'judge_provider_id': 'text', 'new_cooldown_seconds': 0})
        self.bot._store = self.mod.OjStore(Path(self.tmp.name) / 'score.db')
        self.bot._cache = self.mod.CfCache(Path(self.tmp.name) / 'cache.db')

    async def asyncTearDown(self):
        await self.bot.terminate()
        self.tmp.cleanup()

    def event(self, text):
        return stub.AstrMessageEvent(text, group_id='100', sender_id='1', sender_name='A')

    async def test_catalog_refresh_has_early_ack(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def refresh():
            entered.set()
            await release.wait()
            raise RuntimeError('offline')
        self.bot._cf = SimpleNamespace(refresh_catalog=refresh, _catalog=[])
        replies = []
        async def run():
            async for item in self.bot.cmd_new(self.event('new')):
                replies.append(item.text)
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(replies, 'catalog wait must not be silent')
        finally:
            release.set()
            await task

    async def test_cancelled_new_registers_background_delivery(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def fetch(entry):
            entered.set()
            await release.wait()
            return {'cf_key': '4A'}
        self.bot._cf = SimpleNamespace(refresh_catalog=AsyncMock(), pick=lambda tier: {'cf_key': '4A'}, fetch=fetch)
        delivered = asyncio.Event()
        async def deliver(event, scope, pending):
            await pending['task']
            self.bot._pending_preparations.pop(scope, None)
            delivered.set()
        self.bot._deliver_prepared = deliver
        async def run():
            return [item async for item in self.bot.cmd_new(self.event('new'))]
        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.bot._preparation_deliveries)
        release.set()
        await asyncio.wait_for(delivered.wait(), 1)
        self.assertFalse(self.bot._pending_preparations)

    async def test_ask_inflight_guard_is_released_after_completion(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        self.bot.config['ask_cooldown_seconds'] = 0
        async def answer(*args):
            entered.set()
            await release.wait()
            return '回答'
        self.bot._ask_llm = AsyncMock(side_effect=answer)
        async def run():
            return [item.text async for item in self.bot.cmd_ask(self.event('ask 怎么解这道题'))]
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            replies = await run()
            self.assertTrue(any('正在' in text or '处理中' in text for text in replies))
            self.assertEqual(self.bot._ask_llm.await_count, 1)
        finally:
            release.set()
            await task
        await run()
        self.assertEqual(self.bot._ask_llm.await_count, 2)

    async def test_closing_pitfall_receipt_releases_submission_lock(self):
        scope = 'group:100' + self.mod.GRAMMAR_SUFFIX
        self.bot._store.open_problem(scope, {'cf_key': self.mod.PITFALL_PREFIX + 'x'}, '1')
        self.bot._pitfalls = SimpleNamespace(get=lambda key: {'id': 'x', 'answer': 'ok'})
        gen = self.bot.cmd_pitfall(self.event('grm sub 分析'))
        first = await anext(gen)
        self.assertIn('正在判', first.text)
        await gen.aclose()
        self.assertFalse(self.bot._lock_for('grm-submit:group:100:1').locked())

    async def test_same_user_pitfall_submission_is_not_duplicated(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        scope = 'group:100' + self.mod.GRAMMAR_SUFFIX
        self.bot._store.open_problem(scope, {'cf_key': self.mod.PITFALL_PREFIX + 'x'}, '1')
        self.bot._pitfalls = SimpleNamespace(get=lambda key: {'id': 'x', 'answer': 'ok'})
        async def judge(*args, **kwargs):
            entered.set()
            await release.wait()
            return {'verdict': 'WA', 'comment': '重试'}
        self.bot._judge = AsyncMock(side_effect=judge)
        async def run():
            return [item.text async for item in self.bot._grammar_submit(self.event('grm sub x'), '分析')]
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = await run()
            self.assertTrue(any('正在' in text or '处理中' in text for text in second))
            self.assertEqual(self.bot._judge.await_count, 1)
        finally:
            release.set()
            await task
