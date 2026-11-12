"""Judging must see examples and statement figures, and never leak image markers."""
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
import _astrbot_stub as stub

PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='


def problem(**overrides):
    data = {
        'cf_key': '999A', 'title': 'A. Secret', 'rating': 1500,
        'statement_en': 'Move the pin at (0,2). KOUHUIMAGE0END',
        'statement_cn': '把图钉放在 (0,2)。KOUHUIMAGE0END',
        'statement_images': [{'token': 'KOUHUIMAGE0END', 'url': 'https://codeforces.com/x.png', 'src': PNG}],
        'examples': [{'input': '5 0 0 1 1', 'output': '1'}],
        'time_limit': 1.0, 'memory_limit': 256,
    }
    data.update(overrides)
    return data


class JudgeContextTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = stub.load_plugin(Path(tempfile.mkdtemp()))

    def test_prompt_carries_examples_without_internal_markers(self):
        prompt = self.mod.build_judge_prompt(problem(), 'O(1)')
        self.assertIn('样例 1 输入', prompt)
        self.assertIn('5 0 0 1 1', prompt)
        self.assertIn('样例 1 输出', prompt)
        self.assertNotIn('KOUHUIMAGE', prompt)
        self.assertIn('题面图示 1', prompt)
        # Existing privacy rule must survive: no title, key or rating.
        for leaked in ('999A', 'Secret', '1500'):
            self.assertNotIn(leaked, prompt)

    def test_long_examples_are_bounded(self):
        prompt = self.mod.build_judge_prompt(
            problem(examples=[{'input': 'x' * 5000, 'output': 'y' * 5000}]), 'O(1)')
        self.assertIn('样例过长已截断', prompt)
        self.assertLess(len(prompt), 4000)

    def test_missing_or_invalid_figures_are_declared_not_sent(self):
        broken = problem(statement_images=[{'token': 'KOUHUIMAGE0END', 'src': 'https://evil/x.png'}])
        self.assertEqual(self.mod.judge_images(broken), [])
        prompt = self.mod.build_judge_prompt(broken, 'O(1)')
        self.assertIn('图片未能获取', prompt)
        self.assertNotIn('evil', prompt)

    def test_overflowing_figures_are_capped(self):
        records = [{'token': f'KOUHUIMAGE{i}END', 'src': PNG} for i in range(9)]
        self.assertEqual(len(self.mod.judge_images(problem(statement_images=records))),
                         self.mod.JUDGE_IMAGE_LIMIT)

    def test_figure_payload_is_size_bounded(self):
        huge = 'data:image/png;base64,' + 'A' * (self.mod.JUDGE_IMAGE_CHARS // 2 + 10)
        records = [{'token': 'KOUHUIMAGE0END', 'src': huge},
                   {'token': 'KOUHUIMAGE1END', 'src': huge}]
        # One fits the budget, the second would exceed it and stops the list.
        self.assertEqual(self.mod.judge_images(problem(statement_images=records)), [huge])
        oversized = 'data:image/png;base64,' + 'A' * self.mod.JUDGE_IMAGE_CHARS
        self.assertEqual(self.mod.judge_images(problem(statement_images=[
            {'token': 'KOUHUIMAGE0END', 'src': oversized}])), [])

    def test_text_fallback_replaces_markers(self):
        pages = self.mod.KouHuOJ._cf_text_pages(problem())
        text = '\n'.join(pages)
        self.assertNotIn('KOUHUIMAGE', text)
        self.assertIn('题面图示 1', text)
        self.assertIn('样例 1 · 输入', text)

    async def test_judge_attaches_figures_for_vision_model(self):
        sent = []

        async def generate(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(completion_text='{"verdict":"AC","comment":"O(1) 正确"}')

        context = SimpleNamespace(
            get_config=lambda umo=None: {},
            llm_generate=generate,
            get_provider_by_id=lambda pid: SimpleNamespace(provider_config={'modalities': ['text', 'image']}),
        )
        bot = self.mod.KouHuOJ(context, {'judge_provider_id': 'vision'})
        verdict = await bot._judge(stub.AstrMessageEvent('sub'), problem(), 'O(1)', timeout=2)
        self.assertEqual(verdict['verdict'], 'AC')
        self.assertEqual(sent[0]['image_urls'], [PNG])
        self.assertIn('已随本题附图提供', sent[0]['prompt'])

    async def test_text_only_model_gets_no_figures_and_a_clear_prompt(self):
        sent = []

        async def generate(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(completion_text='{"verdict":"WA","comment":"复杂度超限"}')

        context = SimpleNamespace(
            get_config=lambda umo=None: {},
            llm_generate=generate,
            get_provider_by_id=lambda pid: SimpleNamespace(provider_config={'modalities': ['text']}),
        )
        bot = self.mod.KouHuOJ(context, {'judge_provider_id': 'textonly'})
        await bot._judge(stub.AstrMessageEvent('sub'), problem(), 'O(n^2)', timeout=2)
        self.assertNotIn('image_urls', sent[0])
        self.assertIn('本次未附图', sent[0]['prompt'])

    async def test_image_rejection_falls_back_to_text_only_retry(self):
        sent = []

        async def generate(**kwargs):
            sent.append(kwargs)
            if kwargs.get('image_urls'):
                raise RuntimeError('images unsupported by gateway')
            return SimpleNamespace(completion_text='{"verdict":"AC","comment":"O(1) 正确"}')

        context = SimpleNamespace(
            get_config=lambda umo=None: {},
            llm_generate=generate,
            get_provider_by_id=lambda pid: SimpleNamespace(provider_config={'modalities': ['text', 'image']}),
        )
        bot = self.mod.KouHuOJ(context, {'judge_provider_id': 'vision'})
        verdict = await bot._judge(stub.AstrMessageEvent('sub'), problem(), 'O(1)', timeout=3)
        self.assertEqual(verdict['verdict'], 'AC')
        self.assertTrue(sent[0].get('image_urls'))
        self.assertNotIn('image_urls', sent[-1])
        self.assertNotIn('已随本题附图提供', sent[-1]['prompt'])


if __name__ == '__main__':
    unittest.main()
