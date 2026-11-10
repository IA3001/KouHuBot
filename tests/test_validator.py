"""Validator contract tests without compiler dependencies."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('validator', Path(__file__).resolve().parents[1] / 'tools/validate_pitfalls.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)

class ValidatorTests(unittest.TestCase):
    def test_stdout_requires_clean_exit_and_exact_output(self):
        expected = {'kind': 'stdout', 'value': '1'}
        self.assertTrue(v._matches((0, '1\n', ''), expected))
        self.assertFalse(v._matches((0, '10\n', ''), expected))
        self.assertFalse(v._matches((1, '1\n', ''), expected))
        self.assertFalse(v._matches((0, 'extra 1\n', ''), expected))

    def test_line_matching_is_explicit(self):
        expected = {'kind':'stdout_line','value':'count=1'}
        self.assertTrue(v._matches((0, 'header\ncount=1\n', ''), expected))
        self.assertFalse(v._matches((0, 'count=10\n', ''), expected))
        self.assertFalse(v._matches((1, 'count=1\n', ''), expected))

    def test_timeout_is_not_a_crash(self):
        self.assertFalse(v._matches((-9, '', 'TIMEOUT'), {'kind':'crash'}))
        self.assertTrue(v._matches((-11, '', ''), {'kind':'crash'}))

if __name__ == '__main__':
    unittest.main()
