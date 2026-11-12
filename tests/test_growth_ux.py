"""Manual updates, background cooldown, live progress and cancellation regressions."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import _astrbot_stub as stub


class GrowthUXTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = stub.load_plugin(Path(self.tmp.name))
        self.bot = self.m.KouHuOJ(SimpleNamespace(), {'grm_auto_growth': False})
        self.bot._growth = self.m.GrowthBank(Path(self.tmp.name) / 'growth')
        self.bot._pitfalls = SimpleNamespace(all_items=lambda: [], reload=lambda: None)
        self.bot._store = object()
        self.bot._is_admin = lambda event: event.role == 'admin'
        self.bot._provider_id = AsyncMock(return_value=None)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def command(self, message='grm update', role='admin'):
        return [x.text async for x in self.bot.cmd_pitfall(stub.AstrMessageEvent(message, role=role))]

    async def test_failure_backoff_expires_after_sixty_seconds(self):
        await self.bot._grow_pitfalls(None)
        self.assertEqual(self.bot._growth.state['last_outcome'], 'failed')
        self.assertIsNone(self.bot._growth.running)
        self.assertFalse(self.bot._growth_lock.locked())
        self.assertGreater(self.bot._growth.cooldown_remaining(), 59)
        self.bot._growth.state['last_finished'] -= 61
        self.bot._growth.state['last_attempt'] -= 61
        self.assertEqual(self.bot._growth.cooldown_remaining(), 0)
        await self.bot._grow_pitfalls(None)
        self.assertEqual(self.bot._provider_id.await_count, 2)

    async def test_running_task_rejections_never_acknowledge_a_start(self):
        async with self.bot._growth_lock:
            for command in ('grm update', 'grm grow', 'grm grow retry'):
                with self.subTest(command=command):
                    out = await self.command(command)
                    self.assertEqual(len(out), 1)
                    self.assertIn('已有采集任务', out[0])
                    self.assertNotIn('任务已启动', out[0])
        self.bot._provider_id.assert_not_awaited()

    async def test_manual_update_bypasses_all_time_cooldowns(self):
        for outcome in ('success', 'failed', 'cancelled', 'running'):
            with self.subTest(outcome=outcome):
                self.bot._growth.state.update(
                    last_attempt=time.time(), last_finished=time.time(), last_outcome=outcome,
                )
                self.assertGreater(self.bot._growth.cooldown_remaining(), 0)
                self.bot._provider_id.reset_mock()
                out = await self.command()
                self.bot._provider_id.assert_awaited_once()
                self.assertEqual(self.bot._growth.state['last_outcome'], 'failed')
                self.assertIn('grm update', '\n'.join(out))
                self.assertNotIn('60秒', '\n'.join(out))
                self.assertNotIn('retry', '\n'.join(out))
                self.assertFalse(self.bot._growth_lock.locked())

    async def test_failed_update_can_be_repeated_immediately(self):
        for expected in (1, 2):
            out = await self.command()
            self.assertEqual(self.bot._provider_id.await_count, expected)
            self.assertEqual(self.bot._growth.state['last_outcome'], 'failed')
            self.assertIn('grm update', '\n'.join(out))

    async def test_legacy_grow_and_retry_remain_manual_aliases(self):
        for expected, command in enumerate(('grm grow', 'grm grow retry', 'grm grow 重试'), 1):
            with self.subTest(command=command):
                self.bot._growth.state.update(
                    last_attempt=time.time(), last_finished=time.time(), last_outcome='success',
                )
                await self.command(command)
                self.assertEqual(self.bot._provider_id.await_count, expected)

    async def test_manual_update_and_legacy_aliases_require_admin(self):
        for command in ('grm update', 'grm grow', 'grm grow retry'):
            with self.subTest(command=command):
                out = await self.command(command, role='member')
                self.assertEqual(len(out), 1)
                self.assertIn('管理员', out[0])
                self.assertNotIn('任务已启动', out[0])
        self.bot._provider_id.assert_not_awaited()

    async def test_live_status_and_cancel_clear_runtime(self):
        entered = asyncio.Event()
        async def material():
            entered.set()
            await asyncio.Event().wait()
        self.bot._provider_id.return_value = 'mock'
        self.bot._growth.material = material
        task = asyncio.create_task(self.bot._grow_pitfalls(None))
        await entered.wait()
        self.bot._growth.running['started_at'] -= 3
        out = await self.command('grm status')
        self.assertIn('当前阶段：采集资料', out[0])
        self.assertIn('本轮耗时：3 秒', out[0])
        self.assertIn('任务完成后计算', out[0])
        self.assertIn('grm update', out[0])
        self.assertNotIn('retry', out[0])
        blocked = await self.command()
        self.assertEqual(len(blocked), 1)
        self.assertIn('已有采集任务', blocked[0])
        self.bot._provider_id.assert_awaited_once()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.bot._growth.running)
        self.assertFalse(self.bot._growth_lock.locked())
        self.assertEqual(self.bot._growth.state['last_outcome'], 'cancelled')
        self.assertIn('已取消', self.bot._growth.status())
        self.assertIn('当前阶段：空闲', self.bot._growth.status())
        loaded = self.m.GrowthBank(self.bot._growth.directory)
        self.assertIsNone(loaded.running)

    async def test_closing_start_receipt_cancels_owned_task(self):
        entered = asyncio.Event()
        async def provider(event):
            entered.set()
            await asyncio.Event().wait()
        self.bot._provider_id = provider
        # Closing the decorated command must also close the inner generator immediately.
        gen = self.bot.cmd_pitfall(stub.AstrMessageEvent('grm update', role='admin'))
        first = await anext(gen)
        self.assertIn('任务已启动', first.text)
        self.assertTrue(entered.is_set())
        await gen.aclose()
        self.assertIsNone(self.bot._growth.running)
        self.assertFalse(self.bot._growth_lock.locked())

    async def test_terminate_cancels_manual_growth(self):
        async def provider(event):
            await asyncio.Event().wait()
        self.bot._provider_id = provider
        gen = self.bot.cmd_pitfall(stub.AstrMessageEvent('grm update', role='admin'))
        await anext(gen)
        self.bot._store = None
        await self.bot.terminate()
        self.assertIsNone(self.bot._growth.running)
        self.assertFalse(self.bot._growth_lock.locked())
        await gen.aclose()

    async def test_background_keeps_success_interval_and_retries_failure(self):
        for outcome, expected in [('success', 0), ('failed', 1)]:
            self.bot._growth.state.update(last_attempt=time.time()-61, last_outcome=outcome)
            self.bot.config['grm_auto_growth'] = True
            calls = 0
            async def tick(delay):
                nonlocal calls
                self.assertEqual(delay, 60)
                calls += 1
                if calls > 1:
                    raise asyncio.CancelledError
            self.bot._sleep = tick
            self.bot._grow_pitfalls = AsyncMock()
            with self.assertRaises(asyncio.CancelledError):
                await self.bot._growth_loop()
            self.assertEqual(self.bot._grow_pitfalls.await_count, expected)

    async def test_backoff_uses_completion_and_old_state_recovers(self):
        bank = self.bot._growth
        bank.state.update(last_attempt=time.time()-600, last_finished=time.time(), last_outcome='failed')
        self.assertGreater(bank.cooldown_remaining(), 59)
        bank.state.pop('last_finished')
        bank.state.pop('last_outcome')
        self.assertEqual(bank.cooldown_remaining(), 0)
        bank.state.update(last_attempt=time.time(),last_finished=time.time()-600,last_outcome='running')
        bank.save()
        loaded = self.m.GrowthBank(bank.directory)
        self.assertGreater(loaded.cooldown_remaining(), 59)
        self.assertIsNone(loaded.running)

    async def test_success_uses_normal_interval(self):
        self.bot._provider_id.return_value = 'mock'
        self.bot._growth.material = AsyncMock(return_value={'url':'https://oi-wiki.org/lang/csl/', 'text':'material'})
        self.bot._growth.stage = lambda *args: {'review': {}}
        self.bot._growth.finish = lambda *args: self.bot._growth.state.update(last_result='已入库')
        response = SimpleNamespace(completion_text='{}')
        review = SimpleNamespace(completion_text='{"approve":true}')
        with patch.object(self.m, 'generate_with_fallback', new=AsyncMock(side_effect=[response, review])), patch.object(self.m, 'validate_candidate', return_value={'ok':True}):
            await self.bot._grow_pitfalls(None)
        self.assertEqual(self.bot._growth.state['last_outcome'], 'success')
        self.assertGreater(self.bot._growth.cooldown_remaining(), 21599)
        self.assertLessEqual(self.bot._growth.cooldown_remaining(retry=True), 60)


if __name__ == '__main__':
    unittest.main()
