"""Official editorial retrieval tests, entirely offline.

Run: python3 -m unittest discover -s tests -p test_editorial.py
"""
import asyncio
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "editorial", Path(__file__).resolve().parents[1]
    / "plugins/astrbot_plugin_kouhu_oj/sources/editorial.py")
editorial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(editorial)

CONTEST = "https://codeforces.com/contest/1234"
BLOG = "https://codeforces.com/blog/entry/5678"
BODY = ('Sort the array, then scan its elements while maintaining the minimum prefix value. '
        'Every optimal interval is considered when its right endpoint is visited. '
        'Sorting costs O(n log n), and the scan uses O(n) time and O(1) extra space.')


class FakeResponse:
    def __init__(self, html="", status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks if chunks is not None else [html.encode("utf-8")]
        self.closed = False

    def iter_content(self):
        yield from self.chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False
        self.thread_id = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def get(self, url, **kwargs):
        self.thread_id = threading.get_ident()
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class EditorialTests(unittest.TestCase):
    def fetch(self, *responses, timeout=20):
        session = FakeSession(responses)
        module = ModuleType("curl_cffi")
        factory = unittest.mock.Mock(return_value=session)
        module.requests = SimpleNamespace(Session=factory)
        with patch.dict(sys.modules, {"curl_cffi": module}):
            result = asyncio.run(editorial.fetch_official_editorial(1234, timeout=timeout))
        factory.assert_called_once_with(impersonate="chrome")
        self.assertTrue(session.closed)
        self.assertNotEqual(session.thread_id, threading.get_ident())
        self.session = session
        return result

    def test_relative_single_quoted_link_and_nested_body_excludes_comments(self):
        contest = FakeResponse("<a href='/blog/entry/5678'>Round <b>TUTORIAL</b></a>")
        blog = FakeResponse("""<html><head><title>Round &amp; Editorial</title></head>
          <body><div class='ttypography other'><h2>A. Heading</h2>
          <p>First &lt; second &amp; $$$x_i$$$.</p>
          <div><p>Nested body</p><ul><li>One</li><li>Two</li></ul></div>
          <pre>int main() {\n  return 0;\n}<br>  // end</pre>
          <p>Formula: <span class='math'>a^2 + b^2</span></p>
          <!-- secret comment --><script>bad_script()</script><style>bad_style</style>
          </div><div class='comments'><div class='ttypography'>User comment</div></div>
          </body></html>""".replace('<h2>A. Heading</h2>', '<h2>A. Heading</h2><p>' + BODY + '</p>'))
        result = self.fetch(contest, blog, timeout=7)
        self.assertEqual(result["url"], BLOG)
        self.assertEqual(result["title"], "Round & Editorial")
        text = result["text"]
        for expected in ["A. Heading", "First < second & $$$x_i$$$.", "Nested body",
                         "- One", "- Two", "$$$a^2 + b^2$$$",
                         "int main() {\n  return 0;\n}\n  // end"]:
            self.assertIn(expected, text)
        for excluded in ["User comment", "secret comment", "bad_script", "bad_style"]:
            self.assertNotIn(excluded, text)
        self.assertTrue(contest.closed and blog.closed)
        self.assertEqual(self.session.calls, [
            (CONTEST, {"timeout": 7, "stream": True, "allow_redirects": False}),
            (BLOG, {"timeout": 7, "stream": True, "allow_redirects": False})])

    def test_allowed_absolute_and_relative_urls_and_labels(self):
        for href in [BLOG, "/blog/entry/5678", "//codeforces.com/blog/entry/5678",
                     "http://codeforces.com/blog/entry/5678?locale=en#comment", 
                     "https://CODEFORCES.COM/blog/entry/5678/"]:
            for label in ["Editorial", "tutorial", "题解", "РАЗБОР"]:
                with self.subTest(href=href, label=label):
                    result = self.fetch(FakeResponse(f'<a href="{href}">{label}</a>'),
                                        FakeResponse(f'<div class="ttypography"><p>{BODY}</p></div>'))
                    self.assertEqual(result, {"url": BLOG, "text": BODY, "title": ""})

    def test_prioritizes_editorial_tutorial_over_other_language_links(self):
        result = self.fetch(FakeResponse("""
            <a href='/blog/entry/10'>Разбор</a>
            <a href='/blog/entry/11'>Contest announcement</a>
            <a href='/blog/entry/5678'>Editorial</a>"""),
            FakeResponse(f"<div class='ttypography'>{BODY}</div>"))
        self.assertEqual(result["url"], BLOG)

    def test_no_discovered_link_returns_none_without_guessing(self):
        result = self.fetch(FakeResponse("""
            <a href='/blog/entry/9999'>Contest announcement</a>
            <p>The editorial will be posted later.</p>"""))
        self.assertIsNone(result)
        self.assertEqual(len(self.session.calls), 1)

    def test_untrusted_links_are_never_requested(self):
        for href in ["https://evil.test/blog/entry/5678",
                     "//evil.test/blog/entry/5678",
                     "https://codeforces.com.evil.test/blog/entry/5678",
                     "https://codeforces.com@evil.test/blog/entry/5678",
                     "https://evil.test@codeforces.com/blog/entry/5678",
                     "https://codeforces.com:9999/blog/entry/5678",
                     "javascript:alert(1)", "/profile/editorial", "/blog/entry/5678/extra",
                     "/blog/entry/%35%36%37%38", "https://www.codeforces.com/blog/entry/5678"]:
            with self.subTest(href=href):
                self.assertIsNone(self.fetch(FakeResponse(f'<a href="{href}">Editorial</a>')))
                self.assertEqual(len(self.session.calls), 1)

    def test_ignores_script_style_and_comment_links(self):
        self.assertIsNone(self.fetch(FakeResponse("""
            <script><a href='/blog/entry/5678'>Editorial</a></script>
            <style><a href='/blog/entry/5678'>Tutorial</a></style>
            <!-- <a href='/blog/entry/5678'>Editorial</a> -->""")))

    def test_http_blocked_or_redirect_status_raises_and_closes_response(self):
        for status in [403, 429, 500, 302]:
            for on_blog in [False, True]:
                with self.subTest(status=status, on_blog=on_blog):
                    bad = FakeResponse(status=status)
                    responses = [bad]
                    if on_blog:
                        responses.insert(0, FakeResponse("<a href='/blog/entry/5678'>Editorial</a>"))
                    with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                        self.fetch(*responses)
                    self.assertTrue(bad.closed)

    def test_transport_errors_propagate(self):
        with self.assertRaisesRegex(OSError, "network failed"):
            self.fetch(OSError("network failed"))

    def test_missing_empty_or_unclosed_main_body_raises(self):
        for body in ["<p>No body</p>", "<div class='ttypography'>  </div>",
                     "<div class='ttypography'><script>bad</script></div>",
                     "<div class='ttypography'>Unclosed"]:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "body"):
                self.fetch(FakeResponse("<a href='/blog/entry/5678'>Editorial</a>"), FakeResponse(body))

    def test_existing_math_delimiters_are_not_doubled(self):
        result = self.fetch(FakeResponse("<a href='/blog/entry/5678'>Editorial</a>"),
            FakeResponse("<div class='ttypography'><span class='math'>$$$x$$$</span> "
                         f"<span class='math-tex'>y</span><p>{BODY}</p></div>"))
        self.assertEqual(result["text"], "$$$x$$$ $$$y$$$\n\n" + BODY)

    def test_actual_loading_placeholder_is_rejected_before_model_input(self):
        response = FakeResponse("<div class='ttypography'><p>Tutorial is loading...</p></div>")
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            self.fetch(FakeResponse("<a href='/blog/entry/5678'>Tutorial</a>"), response)
        self.assertTrue(response.closed)

    def test_short_and_repeated_placeholder_bodies_are_rejected(self):
        for text in ['Body', 'Complete editorial', 'A. Solution coming soon.',
                     'Tutorial is loading... ' * 20,
                     'Editorial will be posted later. ' * 20,
                     '题解正在加载，请稍后再试。']:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'placeholder|too short'):
                self.fetch(FakeResponse("<a href='/blog/entry/5678'>Tutorial</a>"),
                           FakeResponse(f"<div class='ttypography'>{text}</div>"))

    def test_loading_notice_does_not_hide_a_complete_solution(self):
        result = self.fetch(FakeResponse("<a href='/blog/entry/5678'>Tutorial</a>"),
                            FakeResponse(f"<div class='ttypography'>Tutorial is loading...<p>{BODY}</p></div>"))
        self.assertIn(BODY, result['text'])

    def test_declared_and_streamed_http_size_limits_are_errors(self):
        for response in [FakeResponse(headers={"Content-Length": "17"}),
                         FakeResponse(chunks=[b"12345678", b"123456789"])]:
            with patch.object(editorial, "MAX_HTTP_BYTES", 16):
                with self.assertRaisesRegex(ValueError, "HTTP content exceeds"):
                    self.fetch(response)
            self.assertTrue(response.closed)

    def test_extracted_text_size_limit_is_error_not_truncation(self):
        with patch.object(editorial, "MAX_TEXT_CHARS", 10):
            with self.assertRaisesRegex(ValueError, "text exceeds"):
                self.fetch(FakeResponse("<a href='/blog/entry/5678'>Editorial</a>"),
                           FakeResponse("<div class='ttypography'>01234567890</div>"))

    def test_invalid_contest_ids_are_rejected_before_import_or_network(self):
        for contest_id in [0, -1, True, "1234"]:
            with self.subTest(contest_id=contest_id), self.assertRaises(ValueError):
                asyncio.run(editorial.fetch_official_editorial(contest_id))


if __name__ == "__main__":
    unittest.main()
