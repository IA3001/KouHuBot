"""HTML/tokenizer coverage intentionally needs no Playwright or browser."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import tempfile

_PATH = Path(__file__).resolve().parents[1] / "plugins/astrbot_plugin_kouhu_oj/browser_render.py"
_SPEC = importlib.util.spec_from_file_location("kouhu_browser_render", _PATH)
renderer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = renderer
_SPEC.loader.exec_module(renderer)


class BrowserDiscoveryTests(unittest.TestCase):
    def test_venv_local_matching_revision_without_environment(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            browser=root/'browsers/chromium-123/chrome-linux64/chrome'
            browser.parent.mkdir(parents=True);browser.touch()
            with patch.object(renderer.sys,'prefix',str(root/'venv')):
                missing=SimpleNamespace(executable_path=str(root/'absent/chromium-123/chrome-linux64/chrome'))
                self.assertEqual(renderer._browser_executable(missing),str(browser))
                other=SimpleNamespace(executable_path=str(root/'absent/chromium-124/chrome-linux64/chrome'))
                self.assertIsNone(renderer._browser_executable(other))

class MathTokenizerTests(unittest.TestCase):
    def test_all_delimiters_and_pipes(self):
        source = r"A $$$|x| \le 3$$$ B $$a|b$$ $c$ \(d\) \[e\] end"
        tokens = renderer.tokenize_math(source)
        self.assertEqual("".join(token.raw for token in tokens), source)
        math = [token for token in tokens if token.kind == "math"]
        self.assertEqual([token.text for token in math], [r"|x| \le 3", "a|b", "c", "d", "e"])
        self.assertEqual([token.display for token in math], [False, True, False, False, True])

    def test_broken_mismatched_and_escaped_delimiters(self):
        for source in ["a $$$broken$$ b", "$$broken$", r"\(broken\]", r"cost \$5 and \$6", "a | b | c", "$$$$broken$$$$"]:
            with self.subTest(source=source):
                tokens = renderer.tokenize_math(source)
                self.assertEqual("".join(token.raw for token in tokens), source)
                self.assertTrue(all(token.kind == "text" for token in tokens))

    def test_unmatched_opener_keeps_later_valid_math_and_remainder(self):
        source = r"$$$broken $$a|b$$ tail \(unclosed $c$ remainder"
        tokens = renderer.tokenize_math(source)
        self.assertEqual("".join(token.raw for token in tokens), source)
        self.assertEqual([token.text for token in tokens if token.kind == "math"], ["a|b", "c"])
        self.assertEqual(tokens[-1].text, " remainder")

    def test_multiline_and_literal_html_preserved(self):
        source = '<script>x</script> $$a\n|b|$$ <img onerror="oops">'
        tokens = renderer.tokenize_math(source)
        self.assertEqual("".join(token.raw for token in tokens), source)
        self.assertEqual([t.text for t in tokens if t.kind == "math"], ["a\n|b|"])


class HtmlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name)
        (self.assets / "katex.min.css").write_text('@font-face{src:url(fonts/math.woff2)}', encoding="utf-8")
        (self.assets / "katex.min.js").write_text("/* bundled KaTeX */", encoding="utf-8")

    def build(self, problem, **kwargs):
        with patch.object(renderer, "ASSET_DIR", self.assets):
            return renderer.build_html(problem, **kwargs)

    def test_untrusted_content_is_only_escaped_data(self):
        document = self.build({
            "title": "SECRET TITLE", "cf_key": "SECRET ID", "link": "SECRET URL",
            "statement_cn": '<img src=x onerror=alert(1)>\n\n$"</span><script>alert(2)</script>$',
            "time_limit": '<svg onload="oops">',
        })
        self.assertNotIn("SECRET", document)
        self.assertNotIn("<img src=x", document)
        self.assertNotIn("<script>alert", document)
        self.assertIn("&lt;img src=x", document)
        self.assertIn('data-tex="&quot;&lt;/span&gt;&lt;script&gt;', document)
        self.assertIn("trust: false", document)
        self.assertIn("throwOnError: true", document)
        self.assertIn("catch (_)", document)
        self.assertIn("strict: 'ignore'", document)
        self.assertIn("connect-src 'none'", document)

    def test_markdown_and_code_do_not_parse_math_in_code(self):
        document = self.build({"statement_en": "## Heading\n\n- one $x$\n- two\n\n1. first\n\n```cpp\n$not_math$ <script> | x\n```"})
        self.assertIn("<h2>Heading</h2>", document)
        self.assertIn("<ul>", document)
        self.assertIn("<ol>", document)
        self.assertIn("<pre><code>$not_math$ &lt;script&gt; | x", document)
        self.assertEqual(document.count('data-tex="'), 1)

    def test_multiline_formula_remains_one_token_in_html(self):
        document = self.build({"statement_cn": "before\n\n$$a\n|b|$$\n\nafter"})
        self.assertIn('data-tex="a\n|b|"', document)
        self.assertEqual(document.count('data-tex="'), 1)

    def test_examples_all_preserved_and_escaped(self):
        document = self.build({"examples": [{"input": "$x$ <b> |", "output": "</pre><script>x</script>"}] * 4}, examples=True)
        self.assertIn("样例 4 · 输出", document)
        self.assertIn("$x$ &lt;b&gt; |", document)
        self.assertIn("&lt;/pre&gt;&lt;script&gt;x&lt;/script&gt;", document)
        self.assertNotIn('data-tex="', document)

    def test_local_font_urls_and_no_remote_dependencies(self):
        document = self.build({})
        self.assertIn((self.assets / "katex.min.css").as_uri(), document)
        self.assertIn((self.assets / "katex.min.js").as_uri(), document)
        self.assertNotIn("https://", document)
        self.assertNotIn("http://", document)
        self.assertIn("（这道题没有题面内容）", document)

    def test_empty_examples(self):
        self.assertIn("（这道题没有提供样例）", self.build({}, examples=True))

    def test_build_needs_no_asset_files_and_accepts_width(self):
        with patch.object(renderer, "ASSET_DIR", self.assets / "missing"):
            document = renderer.build_html({}, False, 960)
        self.assertIn("width: 960px", document)
        self.assertIn("#f6f4ec", document)
        self.assertIn("border-left: 3px solid", document)
        self.assertIn("22px/", document)

    def test_section_labels_and_metadata(self):
        problem = {key: "SECRET_" + key for key in ("title", "rating", "tags", "url", "id")}
        problem["statement_cn"] = "内容\n\n【输入】\n输入说明\n输出：\n输出说明\n数据范围\n1 <= n"
        document = self.build(problem)
        self.assertNotIn("SECRET_", document)
        for label in ("题面", "输入", "输出", "数据范围"):
            self.assertIn(f"<h2>{label}</h2>", document)
        self.assertIn("element.textContent =", document)

    def test_limits_are_visible_and_missing_is_explicit(self):
        for examples in (False, True):
            document = self.build({'time_limit':2,'memory_limit':256}, examples=examples)
            self.assertIn('时间限制：2 秒', document)
            self.assertIn('内存限制：256 MB', document)
        self.assertIn('未提供', self.build({}))

    def test_examples_keep_whitespace_and_more_than_three(self):
        text = "  1\n\n  <x>\t$x$ &\n"
        document = self.build({"examples": [{"input": text, "output": "0"}] * 6}, examples=True)
        self.assertEqual(document.count("<pre><code>"), 12)
        self.assertIn("  1\n\n  &lt;x&gt;\t$x$ &amp;\n", document)
        self.assertIn("样例 6", document)


class WorkerTests(unittest.TestCase):
    def test_sync_entrypoints_use_one_other_thread_inside_event_loop(self):
        import asyncio
        caller = threading.get_ident()
        seen = []
        def fake_render(problem, directory, width, examples):
            seen.append((threading.get_ident(), examples, width))
            return "image.png"
        async def invoke():
            self.assertEqual(renderer.render_statement({}, width=800), "image.png")
            self.assertEqual(renderer.render_examples({}), "image.png")
        with patch.object(renderer, "_render", side_effect=fake_render):
            asyncio.run(invoke())
        self.assertNotEqual(seen[0][0], caller)
        self.assertEqual(seen[0][0], seen[1][0])
        self.assertEqual([row[1:] for row in seen], [(False, 800), (True, 900)])

    def test_invalid_width_fails_before_browser_import(self):
        for width in [0, 319, 4097, True, "900"]:
            with self.subTest(width=width), self.assertRaises(ValueError):
                renderer.render_statement({}, width=width)


if __name__ == "__main__":
    unittest.main()
