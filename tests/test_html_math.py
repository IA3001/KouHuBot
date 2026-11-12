"""Legacy Codeforces HTML math retains exponents and subscripts."""
import logging
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_kouhu_oj'))
with patch.dict(sys.modules, {'astrbot': types.ModuleType('astrbot'), 'astrbot.api': types.SimpleNamespace(logger=logging.getLogger('html-math'))}):
    from sources.codeforces import _text_of, parse_statement, protect_tex, restore_tex


class HtmlMathTest(unittest.TestCase):
    def test_standalone_sup_and_sub_keep_bases(self):
        self.assertEqual(_text_of('At most 10<sup>5</sup> values x<sub>i</sub>.'),
                         'At most $$$10^{5}$$$ values $$$x_{i}$$$.')
        self.assertEqual(_text_of('x<sub>i</sub><sup>2</sup>'), '$$$x_{i}^{2}$$$')

    def test_live_cf507b_input_markup(self):
        # Retrieved from /problemset/problem/507/B: actual legacy tex-span/sup HTML.
        source = '''<p>The first line has <span class="tex-span"><i>r</i></span> (<span class="tex-span">1	≤	<i>r</i>	≤	10<sup class="upper-index">5</sup></span>, <span class="tex-span">	-	10<sup class="upper-index">5</sup>	≤	<i>x</i>,	<i>y</i>,	<i>x</i>',	<i>y</i>'	≤	10<sup class="upper-index">5</sup></span>).</p>'''.replace('\t', '\u2009')
        text = _text_of(source)
        self.assertEqual(text.count('10^{5}'), 3)
        self.assertNotIn('105', text)
        self.assertIn(r'\leq', text)
        protected, formulas = protect_tex(text)
        self.assertEqual(len(formulas), 3)
        self.assertEqual(restore_tex(protected, formulas), text)

    def test_nested_index_markup_and_class_indices(self):
        self.assertEqual(_text_of('<span class="tex-span"><i>a</i><sub><i>i</i>+1</sub><sup>2</sup></span>'),
                         '$$$a_{i+1}^{2}$$$')
        self.assertEqual(_text_of('<span class="tex-span">10<span class="upper-index">5</span> + a<span class="lower-index">i</span></span>'),
                         '$$$10^{5} + a_{i}$$$')
        self.assertEqual(_text_of('<span class="tex-span">x<span class="sup">2</span><span class="sub">i</span></span>'),
                         '$$$x^{2}_{i}$$$')

    def test_existing_math_and_literal_entities_unchanged(self):
        source = '<p>Require $$$a < b$$$ and $$$$$$x &lt;= 10^5$$$$$$; a &lt; b &amp; b &lt;= c.</p>'
        self.assertEqual(_text_of(source), 'Require $$$a < b$$$ and $$$$$$x <= 10^5$$$$$$; a < b & b <= c.')
        self.assertEqual(_text_of('<span class="tex-span">$$$x^2$$$</span>'), '$$$x^2$$$')

    def test_prose_code_and_samples_are_preserved(self):
        source = '<p>A <b>bold</b> line<br>next.</p><pre>if (x &lt; 10) {\n  x &lt;&lt;= 1;\n}</pre><p>10<sup>5</sup></p>'
        text = _text_of(source)
        self.assertIn('A bold line\nnext.', text)
        self.assertIn('```\nif (x < 10) {\n  x <<= 1;\n}\n```', text)
        self.assertIn('$$$10^{5}$$$', text)
        parsed = parse_statement('<div class="problem-statement"><div><p>10<sup>5</sup></p></div><div class="sample-tests"><div class="input"><pre>1 &lt; 2<br/>  3</pre></div><div class="output"><pre>105</pre></div></div></div>')
        self.assertEqual(parsed['statement_en'], '$$$10^{5}$$$')
        self.assertEqual(parsed['examples'], [{'input': '1 < 2\n  3', 'output': '105'}])

    def test_math_entities_and_nested_spans(self):
        self.assertEqual(_text_of('<span class="tex-span"><span>n</span> &lt; 10<sup>5</sup> &amp; x &ge; 0</span>'),
                         r'$$$n < 10^{5} & x \geq  0$$$')


if __name__ == '__main__':
    unittest.main()
