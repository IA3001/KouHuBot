"""Ordered fallback requests must share one budget and preserve cancellation."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import _astrbot_stub as stub

stub.install_stubs()
spec = importlib.util.spec_from_file_location(
    'llm_fallback_tested', Path(__file__).resolve().parents[1]
    / 'plugins/astrbot_plugin_kouhu_oj/llm_fallback.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def response(text='ok'):
    return SimpleNamespace(completion_text=text)


class Context:
    def __init__(self, fallback_ids=None):
        self.config = {'agent_runner': {'config': {'model': {
            'fallback_provider_ids': fallback_ids if fallback_ids is not None else []}}}}
        self.config_calls = []
        self.calls = []
        self.actions = {}
        self.providers = {}

    def get_config(self, **kwargs):
        self.config_calls.append(kwargs)
        return self.config

    def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)

    async def llm_generate(self, *, chat_provider_id, **kwargs):
        self.calls.append((chat_provider_id, kwargs))
        action = self.actions.get(chat_provider_id, response())
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            return await action()
        return action


class FallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_config_order_duplicates_and_kwargs(self):
        ctx = Context(['primary', 'second', 'second', 'third'])
        ctx.actions = {'primary': RuntimeError('offline'), 'second': RuntimeError('offline')}
        event = SimpleNamespace(unified_msg_origin='qq:group:123')
        got = await module.generate_with_fallback(
            ctx, event=event, chat_provider_id='primary', timeout=1,
            prompt='private input', system_prompt='private system')
        self.assertEqual(got.completion_text, 'ok')
        self.assertEqual(ctx.config_calls, [{'umo': 'qq:group:123'}])
        self.assertEqual([x[0] for x in ctx.calls], ['primary', 'second', 'third'])
        self.assertEqual(ctx.calls[-1][1], {'prompt': 'private input',
                                            'system_prompt': 'private system',
                                            'request_max_retries': module.REQUEST_MAX_RETRIES})

    async def test_slow_primary_times_out_before_backup_budget(self):
        ctx = Context(['backup'])
        cancelled = asyncio.Event()
        async def stuck():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        ctx.actions['primary'] = stuck
        got = await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=.08)
        self.assertEqual(got.completion_text, 'ok')
        self.assertTrue(cancelled.is_set())
        self.assertEqual([x[0] for x in ctx.calls], ['primary', 'backup'])

    async def test_fast_failure_gives_next_remaining_budget(self):
        ctx = Context(['backup'])
        ctx.actions['primary'] = RuntimeError('offline')
        async def slow_backup():
            await asyncio.sleep(.02)
            return response()
        ctx.actions['backup'] = slow_backup
        got = await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=.1)
        self.assertEqual(got.completion_text, 'ok')

    async def test_no_fallback_gives_primary_full_budget(self):
        ctx = Context()
        async def slow():
            await asyncio.sleep(.04)
            return response()
        ctx.actions['primary'] = slow
        self.assertEqual((await module.generate_with_fallback(
            ctx, chat_provider_id='primary', timeout=.08)).completion_text, 'ok')
        self.assertEqual(ctx.config_calls, [{}])

    async def test_empty_and_validator_rejection_advance(self):
        ctx = Context(['second', 'third'])
        ctx.actions = {'primary': response(' '), 'second': response('bad'), 'third': response('valid')}
        got = await module.generate_with_fallback(
            ctx, chat_provider_id='primary', timeout=1,
            validate=lambda result: result.completion_text == 'valid')
        self.assertEqual(got.completion_text, 'valid')
        self.assertEqual(len(ctx.calls), 3)

    async def test_last_error_and_validation_error_propagate(self):
        ctx = Context(['second'])
        ctx.actions['primary'] = RuntimeError('offline')
        error = LookupError('unavailable id')
        ctx.actions['second'] = error
        with self.assertRaises(LookupError) as raised:
            await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1)
        self.assertIs(raised.exception, error)
        ctx = Context()
        with self.assertRaises(ValueError):
            await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1,
                                              validate=lambda result: False)

    async def test_cancellation_never_uses_fallback(self):
        ctx = Context(['backup'])
        ctx.actions['primary'] = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1)
        self.assertEqual([x[0] for x in ctx.calls], ['primary'])

    async def test_image_skips_known_incompatible_and_allows_unknown(self):
        ctx = Context(['text', 'unknown', 'vision'])
        ctx.providers = {'primary': SimpleNamespace(provider_config={'modalities': ['text']}),
                         'text': SimpleNamespace(provider_config={'modalities': []}),
                         'vision': SimpleNamespace(provider_config={'modalities': ['text', 'image']})}
        ctx.actions['unknown'] = RuntimeError('not found')
        images = ['data:image/png;base64,private']
        await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1,
                                           image_urls=images)
        self.assertEqual([x[0] for x in ctx.calls], ['unknown', 'vision'])
        self.assertEqual(ctx.calls[-1][1]['image_urls'], images)

    async def test_malformed_or_missing_config_is_primary_only(self):
        for bad in ({}, None, {'agent_runner': []},
                    {'agent_runner': {'config': {'model': {'fallback_provider_ids': 'backup'}}}},
                    {'agent_runner': {'config': {'model': {'fallback_provider_ids': ['backup', None]}}}}):
            ctx = Context()
            ctx.config = bad
            await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1)
            self.assertEqual([x[0] for x in ctx.calls], ['primary'])
        ctx = Context()
        ctx.get_config = Mock(side_effect=RuntimeError('unavailable'))
        await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1)
        self.assertEqual(len(ctx.calls), 1)

    async def test_deadline_and_no_eligible_models(self):
        ctx = Context()
        with self.assertRaises(asyncio.TimeoutError):
            await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=0)
        self.assertFalse(ctx.calls)
        ctx.providers['primary'] = SimpleNamespace(provider_config={'modalities': ['text']})
        with self.assertRaises(ValueError):
            await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1,
                                               image_urls=['image'])
        self.assertFalse(ctx.calls)

    async def test_primary_gets_more_than_an_equal_share(self):
        # 90s / 4 个候选：主模型 30s（高于均分的 22.5s），后三个各留 20s。
        self.assertEqual(module.attempt_budgets(90, 4), [30])
        self.assertEqual(module.attempt_budgets(90, 3), [50])
        self.assertEqual(module.attempt_budgets(135, 2), [115])
        self.assertEqual(module.attempt_budgets(15, 1), [15])

    async def test_every_candidate_keeps_a_real_window(self):
        """主模型快速失败后，剩下的时间够每个候选各拿满 20 秒。"""
        self.assertEqual(module.attempt_budgets(60, 3), [20])
        self.assertEqual(module.attempt_budgets(40, 2), [20])
        # 20 秒是一个模型真正答完判题 JSON 的下限（今天实测最慢的健康模型 11.6s）。
        self.assertEqual(module.ATTEMPT_FLOOR_SECONDS, 20)

    async def test_tiny_budget_falls_back_to_an_equal_split(self):
        # 总预算连每个候选 20 秒都排不出来时，均分就是最好的方案。
        self.assertEqual(module.attempt_budgets(60, 4), [15])
        self.assertAlmostEqual(module.attempt_budgets(20, 3)[0], 20 / 3)

    async def test_upstream_5xx_does_not_burn_the_whole_window(self):
        """上游 503 由降级链接管，而不是 AstrBot 内部重试 5 次。"""
        ctx = Context(['backup'])
        ctx.actions['primary'] = RuntimeError('503 Service temporarily unavailable')
        got = await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=1)
        self.assertEqual(got.completion_text, 'ok')
        self.assertEqual([x[0] for x in ctx.calls], ['primary', 'backup'])
        for _, kwargs in ctx.calls:
            self.assertEqual(kwargs['request_max_retries'], module.REQUEST_MAX_RETRIES)

    async def test_last_fallback_never_gets_a_dead_zero_window(self):
        """主模型和中间候选把 90s 吃干净后，最后的兜底模型仍要拿到能跑完的窗口。

        现场（2026-11-17 09:54）：sol 45s 超时 → terra 22s 503 → supabays 只剩 11s
        → deepseek-flash 只剩 11s，全灭。用户的原话是「最兜底的那个肯定可以调用啊」。
        """
        ctx = Context(['mid', 'last'])
        async def stuck():
            await asyncio.Event().wait()
        ctx.actions['primary'] = stuck
        ctx.actions['mid'] = stuck
        async def slow_last():
            await asyncio.sleep(.01)
            return response('ok')
        ctx.actions['last'] = slow_last
        got = await module.generate_with_fallback(ctx, chat_provider_id='primary', timeout=.08)
        self.assertEqual(got.completion_text, 'ok')
        # 主模型和中间候选都超时后，最后的兜底模型照样拿到一次真调用。
        self.assertEqual([x[0] for x in ctx.calls], ['primary', 'mid', 'last'])


if __name__ == '__main__':
    unittest.main()
