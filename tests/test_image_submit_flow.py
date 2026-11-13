"""Exercise vision → text judging with round changes and retry semantics."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

import _astrbot_stub as stub


class ImageSubmitFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.context = type('Context', (), {})()
        self.context.llm_generate = AsyncMock(return_value=type('Response', (), {
            'completion_text': json.dumps({'submission': '扫描数组并维护最大值', 'confidence': 'high', 'notes': ''})})())
        self.bot = self.mod.KouHuOJ(self.context, {'judge_provider_id': 'text', 'vision_provider_id': 'vision', 'judge_ack_delay_seconds': 0})
        self.bot._store = self.mod.OjStore(Path(self.tmp.name) / 'score.db')
        self.bot._cache = self.mod.CfCache(Path(self.tmp.name) / 'cache.db')
        self.bot._cf = object()
        self.problem = {'cf_key': '4A', 'contest_id': 4, 'idx': 'A', 'tier': 800, 'points': 100,
                        'statement_en': 'Find max', 'statement_cn': '求最大值'}
        self.bot._cache.save(self.problem)
        self.bot._store.open_problem('group:100200300', self.problem, '1001')
        self.bot._judge = AsyncMock(return_value={'verdict': 'WA', 'comment': '需要说明边界'})

    async def asyncTearDown(self):
        self.bot._store.close()
        self.bot._cache.close()
        self.tmp.cleanup()

    async def submit(self, text='sub', images=True):
        with patch.object(self.mod, 'collect_submission_images', return_value=[object()] if images else []), \
             patch.object(self.mod, 'prepare_image_urls', new=AsyncMock(return_value=['data:image/png;base64,abc'])):
            return '\n'.join([x.text async for x in self.bot.cmd_submit(stub.AstrMessageEvent(text))])

    async def test_image_runs_vision_then_existing_judge_and_saves_text(self):
        out = await self.submit('sub 补充说明')
        self.assertIn('图片转写预览', out)
        self.assertIn('WA', out)
        args = self.bot._judge.call_args.args
        self.assertIn('扫描数组并维护最大值', args[2])
        self.assertIn('补充说明', args[2])
        self.assertEqual(self.context.llm_generate.call_args.kwargs['chat_provider_id'], 'vision')
        self.assertEqual(self.context.llm_generate.call_args.kwargs['image_urls'], ['data:image/png;base64,abc'])
        row = self.bot._store._conn.execute('SELECT submission FROM review_entries').fetchone()
        self.assertEqual(row[0], args[2])

    async def test_image_caption_uses_combined_limit_and_disabled_blocks_vision(self):
        self.assertIn('WA', await self.submit('sub ' + '补充' * 1100))
        self.bot.config['image_submission_enabled'] = False
        calls = self.context.llm_generate.await_count
        self.assertIn('尚未开启', await self.submit())
        self.assertEqual(self.context.llm_generate.await_count, calls)

    async def test_low_confidence_does_not_judge_or_count(self):
        self.context.llm_generate.return_value.completion_text = json.dumps({'submission': '不清楚', 'confidence': 'low', 'notes': '符号模糊'})
        out = await self.submit()
        self.assertIn('不计作答', out)
        self.bot._judge.assert_not_awaited()
        self.assertEqual(self.bot._store._conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)

    async def test_round_change_during_vision_discards_submission(self):
        response = self.context.llm_generate.return_value
        async def change(**kwargs):
            self.bot._store.close_problem('group:100200300', 'gave_up', '1002')
            self.bot._store.open_problem('group:100200300', self.problem, '1002')
            return response
        self.context.llm_generate.side_effect = change
        out = await self.submit()
        self.assertIn('正题已结束或更换', out)
        self.bot._judge.assert_not_awaited()

    async def test_judge_failure_retry_reuses_long_transcript_without_vision(self):
        text = '维护最大值。' * 500
        self.context.llm_generate.return_value.completion_text = json.dumps({'submission': text, 'confidence': 'high', 'notes': ''})
        self.bot._judge.return_value = None
        self.assertIn('sub retry', await self.submit())
        self.bot._judge.return_value = {'verdict': 'WA', 'comment': '继续完善'}
        self.assertIn('WA', await self.submit('sub retry', images=False))
        self.assertEqual(self.context.llm_generate.await_count, 1)
        self.assertEqual(self.bot._judge.call_args.args[2], text)

    async def test_bare_sub_is_routed_and_text_path_still_works(self):
        self.assertIsNotNone(self.mod.RE_SUBMIT.fullmatch('sub'))
        self.assertIsNone(self.mod.RE_SUBMIT.fullmatch('submarine'))
        hint = await self.submit(images=False)
        self.assertIn('引用自己那条消息', hint)
        self.assertIn('作答图片', hint)
        self.assertIn('WA', await self.submit('sub 扫描数组', images=False))
        self.context.llm_generate.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
