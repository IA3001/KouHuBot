"""Ambiguous model prose and malformed enums must never award an AC."""
from pathlib import Path
import tempfile
import unittest

import _astrbot_stub as stub


class StrictVerdictTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exact_json_enum_and_case_normalization(self):
        for verdict in ('AC', 'WA'):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.mod.parse_verdict('{"verdict":"' + verdict.lower() + '","comment":"评语"}'),
                                 {'verdict': verdict, 'comment': '评语', 'counterexample': ''})
        self.assertEqual(self.mod.parse_verdict('```json\n{"verdict":"AC","comment":"正确"}\n```')['verdict'], 'AC')
        self.assertEqual(self.mod.parse_verdict('{"verdict":"PARTIAL","comment":"还差一步"}', self.mod.PITFALL_VERDICTS)['verdict'], 'PARTIAL')
        self.assertIsNone(self.mod.parse_verdict('{"verdict":"PARTIAL"}', self.mod.JUDGE_VERDICTS))

    def test_substrings_and_negated_json_values_are_invalid(self):
        for verdict in ('NOT AC', 'WA or AC', 'ACCEPTED', 'AC/WA', 'maybe AC', 'not_wa', 'AC 正确', 'WA - wrong'):
            with self.subTest(verdict=verdict):
                self.assertIsNone(self.mod.parse_verdict('{"verdict":"' + verdict + '","comment":"x"}'))

    def test_ambiguous_and_negated_prose_is_never_a_verdict(self):
        for raw in ('不能判 AC，应该是 WA。', '我觉得这题应该是 AC 吧', 'NOT AC',
                    'AC or WA', 'AC\n不是正确解法', 'WA\nAC', 'AC\n复杂度还需要验证',
                    'The answer is not AC.', '最终结论 WA，原先 AC 有误。', '判定结果：AC'):
            with self.subTest(raw=raw):
                self.assertIsNone(self.mod.parse_verdict(raw))

    def test_standalone_enum_is_the_only_prose_fallback(self):
        for verdict in ('AC', 'WA'):
            self.assertEqual(self.mod.parse_verdict('  ' + verdict.lower() + '\n')['verdict'], verdict)
        self.assertIsNone(self.mod.parse_verdict('PARTIAL'))
        self.assertEqual(self.mod.parse_verdict('PARTIAL', self.mod.PITFALL_VERDICTS)['verdict'], 'PARTIAL')

    def test_invalid_or_ambiguous_json_is_not_rescanned_for_keywords(self):
        for raw in ('{"verdict":"AC",}', '{"verdict":"WA","comment":"AC"',
                    '{"verdict":"WA","verdict":"AC"}', '[{"verdict":"AC"}]',
                    '{"verdict":true}', '{"verdict":null}', '{"verdict":"AC","comment":{}}',
                    '{"verdict":"AC"} trailing WA', '{"verdict":"AC"}{"verdict":"WA"}',
                    '判定结果：{"verdict":"AC"}', 'NOT {"verdict":"AC"}', '', None):
            with self.subTest(raw=raw):
                self.assertIsNone(self.mod.parse_verdict(raw))


if __name__ == '__main__':
    unittest.main()
