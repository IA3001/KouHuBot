"""Verify translation and judging use AstrBot's existing session fallback list."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import _astrbot_stub as stub


class PluginFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.calls = []
        self.umos = []
        def config(umo=None):
            self.umos.append(umo)
            return {'agent_runner': {'config': {'model': {'fallback_provider_ids': ['backup', 'last']}}}}
        async def generate(**kwargs):
            self.calls.append(kwargs['chat_provider_id'])
            if kwargs['chat_provider_id'] == 'primary':
                raise ConnectionError('unavailable')
            return SimpleNamespace(completion_text=self.reply)
        self.context = SimpleNamespace(get_config=config, llm_generate=generate)
        self.bot = self.mod.KouHuOJ(self.context, {'judge_provider_id': 'primary', 'translate_timeout_seconds': 1})
        self.event = stub.AstrMessageEvent('new', umo='qq:session:group')

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_translation_uses_session_fallback_including_batch(self):
        self.reply = '给定一个整数数组。'
        token = self.bot._translation_event.set(self.event)
        try:
            self.assertEqual(await self.bot._translate_statement('Given an array.'), self.reply)
            self.assertEqual(self.calls, ['primary', 'backup'])
            self.assertEqual(self.umos, ['qq:session:group'])
            self.calls.clear()
            self.reply = '{"1":"给定数组"}'
            await self.bot._translate_statement('KOUHU_TRANSLATION_BATCH\n{"1":"Given an array"}')
            self.assertEqual(self.calls, ['primary', 'backup'])
        finally:
            self.bot._translation_event.reset(token)

    async def test_judge_falls_back_and_preserves_verdict(self):
        self.reply = json.dumps({'verdict': 'WA', 'comment': '边界有误'})
        verdict = await self.bot._judge(self.event, {'statement_en': 'Find max'}, 'sort', timeout=1)
        self.assertEqual(verdict['verdict'], 'WA')
        self.assertEqual(self.calls, ['primary', 'backup'])

    async def test_judge_invalid_primary_output_uses_backup(self):
        async def generate(**kwargs):
            self.calls.append(kwargs['chat_provider_id'])
            return SimpleNamespace(completion_text='无法确定' if kwargs['chat_provider_id'] == 'primary' else '{"verdict":"WA","comment":"错误"}')
        self.context.llm_generate = generate
        verdict = await self.bot._judge(self.event, {}, 'answer', timeout=1)
        self.assertEqual(verdict['verdict'], 'WA')
        self.assertEqual(self.calls, ['primary', 'backup'])


if __name__ == '__main__':
    unittest.main()
