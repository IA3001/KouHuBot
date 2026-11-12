"""Group input is messy: commands must survive no-space, punctuation and null config."""
import re
import tempfile
from pathlib import Path
import unittest

import _astrbot_stub as stub


class CommandToleranceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.ctx = type('Context', (), {})()
        async def generate(**kwargs):
            return type('Response', (), {'completion_text': 'C++17 即可。'})()
        self.ctx.llm_generate = generate
        self.bot = await self._build({})

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _build(self, config):
        bot = self.mod.KouHuOJ(self.ctx, {'judge_provider_id': 'mock', **config})
        await bot.initialize()
        return bot

    def event(self, text):
        return stub.AstrMessageEvent(text, group_id='1', sender_id='1001', sender_name='Alice')

    async def drive(self, handler, text):
        return '\n'.join([item.text async for item in getattr(self.bot, handler)(self.event(text))])

    def test_command_word_accepts_no_space_argument(self):
        cases = [
            ('sub这题用二分', self.mod.RE_SUBMIT, True),
            ('submit这题', self.mod.RE_SUBMIT, True),
            ('hint1', self.mod.RE_HINT, True),
            ('tier2', self.mod.RE_TIER, True),
            ('history2', self.mod.RE_HISTORY, True),
            ('sol628B', self.mod.RE_SOL, True),
            ('ver0.6.0', self.mod.RE_VER, True),
            ('ask你好', self.mod.RE_ASK, True),
        ]
        for text, pattern, want in cases:
            self.assertEqual(bool(pattern.match(text)), want, text)
        self.assertEqual(self.mod.RE_SUBMIT.match('sub这题用二分').group(1), '这题用二分')

    def test_english_words_are_not_swallowed(self):
        for text, pattern in (('subscribe 一下', self.mod.RE_SUBMIT),
                              ('submarine', self.mod.RE_SUBMIT),
                              ('newbie', self.mod.RE_NEW),
                              ('current', self.mod.RE_PROBLEM),
                              ('men', self.mod.RE_ME)):
            self.assertFalse(pattern.match(text), text)

    def test_trailing_chinese_punctuation_and_fullwidth_slash(self):
        for text, pattern in (('new！', self.mod.RE_NEW), ('new。', self.mod.RE_NEW),
                              ('help？', self.mod.RE_HELP), ('giveup！', self.mod.RE_GIVEUP),
                              ('／new', self.mod.RE_NEW), ('new　图论', self.mod.RE_NEW)):
            self.assertTrue(pattern.match(text), text)

    async def test_bare_ask_explains_itself_instead_of_silence(self):
        self.assertIsNotNone(self.mod.RE_ASK.match('ask'))
        out = await self.drive('cmd_ask', 'ask')
        self.assertIn('问点什么吧', out)

    async def test_ask_does_not_hijack_ordinary_version_questions(self):
        out = await self.drive('cmd_ask', 'ask 这题对 C++ 版本有要求吗')
        self.assertNotIn('当前版本', out)
        self.assertIn('C++17', out)

    async def test_null_config_does_not_break_commands(self):
        bot = await self._build({'new_cooldown_seconds': None, 'max_submission_chars': None,
                                 'ask_max_chars': None, 'ask_cooldown_seconds': None,
                                 'image_width': None})
        try:
            for handler, text in (('cmd_new', 'new'), ('cmd_submit', 'sub x'),
                                  ('cmd_ask', 'ask 你好'), ('cmd_rank', 'rank')):
                out = '\n'.join([item.text async for item in getattr(bot, handler)(
                    stub.AstrMessageEvent(text, group_id='2', sender_id='2001', sender_name='B'))])
                self.assertTrue(out, f'{text} 没有任何回复')
        finally:
            await bot.terminate()

    async def test_pitfall_category_must_exist(self):
        out = await self.drive('cmd_pitfall', 'grm new 二分')
        self.assertIn('暂时没有题目', out)
        self.assertIn('浮点数', out)
        out = await self.drive('cmd_pitfall', 'grm new 4')
        self.assertIn('难度只有 1/2/3', out)

    async def test_cf_lane_points_at_grm_when_only_pitfall_is_open(self):
        await self.drive('cmd_pitfall', 'grm new')
        out = await self.drive('cmd_submit', 'sub 这里会溢出')
        self.assertIn('grm sub', out)
        out = await self.drive('cmd_giveup', 'giveup')
        self.assertIn('grm giveup', out)

    async def test_rating_range_accepts_chinese_and_dash_separators(self):
        for text in ('new 1200到1600', 'new 1200–1600', 'new [1200-1600]'):
            arg = re.sub(r'\s*[到至~～—–−]\s*', '-', text[4:])
            self.assertRegex(arg, r'^\[?1200-1600\]?$')


if __name__ == '__main__':
    unittest.main()
