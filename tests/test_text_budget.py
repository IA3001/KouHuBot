"""User-facing docs must stay short and free of raw Markdown."""
import tempfile
from pathlib import Path
import unittest
import _astrbot_stub as stub


class TextBudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls.tmp.name))
        cls.caps = __import__('capabilities')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_help_fits_one_short_message(self):
        text = self.mod.HELP_TEXT
        self.assertLessEqual(len(text), 1000)
        self.assertLessEqual(text.count('\n'), 34)
        # Every command still documented after compaction.
        for command in ('new tags', 'sub retry', 'grm status', 'grm update',
                        'history show', 'sol 628B', 'ver list', 'tier', 'status'):
            self.assertIn(command, text)

    def test_intro_is_short_and_ordered(self):
        text = self.caps.INTRO_TEXT
        self.assertLessEqual(len(text), 700)
        self.assertIn('还在计划中', text)
        # Numbering must be sequential: the disclaimer comes after all points.
        numbers = [f'{sign}' for sign in '①②③④⑤⑥' if f'{sign} ' in text]
        self.assertEqual(numbers, list('①②③④⑤⑥'))
        self.assertLess(text.index('⑥'), text.index('模型判题和提示可能出错'))

    def test_rules_groups_submission_under_its_own_section(self):
        text = self.caps.rules_text()
        self.assertLessEqual(len(text), 1300)
        for fact in ('首次提交即 AC', '不扣分', '30%', '10%'):
            self.assertIn(fact, text)
        draw = text.index('🎲 抽题')
        submit = text.index('📝 提交与判题')
        self.assertLess(draw, submit)
        # sub retry used to be misfiled inside the drawing section.
        self.assertGreater(text.index('sub retry'), submit)
        self.assertLess(text.index('sub retry'), text.index('💡 提示与独立线路'))

    def test_changelog_renders_as_plain_text(self):
        changelog = '# 更新日志\n\n## 9.9.9\n\n摘要：测试版本摘要。\n\n- 第一条 `代码` 说明\n- 第二条 **加粗** 说明\n\n---\n'
        body = self.mod._plain_changelog(changelog, '9.9.9')
        self.assertIn('📌 9.9.9 · 测试版本摘要。', body)
        self.assertIn('· 第一条 代码 说明', body)
        self.assertIn('· 第二条 加粗 说明', body)
        for junk in ('## ', '- ', '**', '`', '---'):
            self.assertNotIn(junk, body)
        self.assertEqual(self.mod._plain_changelog(changelog, '1.0.0'), '')

    def test_changelog_section_is_truncated_politely(self):
        section = '## 9.9.9\n\n' + '\n'.join(f'- 第 {index} 条较长的变更说明内容' for index in range(80))
        body = self.mod._plain_changelog('# 日志\n\n' + section, '9.9.9', limit=300)
        self.assertLessEqual(len(body), 360)
        self.assertIn('完整内容见仓库', body)

    def test_clip_text_cuts_on_sentence_boundary(self):
        sentence = '这是一句用来测试截断的完整中文回答内容，长度刚好够用。'
        text = sentence * 20
        clipped = self.mod._clip_text(text, 300)
        body, _, note = clipped.partition('\n')
        self.assertLessEqual(len(body), 300)
        self.assertTrue(body.endswith('。'), body[-12:])
        self.assertIn('已截断', note)
        self.assertEqual(self.mod._clip_text('短回答。', 300), '短回答。')
        # 没有标点可切时也要给截断说明，而不是硬切
        self.assertIn('已截断', self.mod._clip_text('字' * 500, 100))


if __name__ == '__main__':
    unittest.main()
