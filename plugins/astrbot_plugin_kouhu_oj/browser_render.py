"""Offline Chromium/KaTeX cards, with a synchronous API safe in async callers.

Playwright is deliberately imported only in the worker, so HTML generation and
its tests need neither Playwright nor an installed browser.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import html
from pathlib import Path
import re
import sys
import tempfile
import threading
from urllib.parse import unquote, urlsplit
import uuid

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "katex"
MAX_HEIGHT = 30_000
_RENDER_LOCK = threading.Lock()
_RENDERER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kouhu-browser")
_DELIMITERS = (("$$$", "$$$", False), ("$$", "$$", True),
               ("$", "$", False), (r"\(", r"\)", False),
               (r"\[", r"\]", True))


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    raw: str
    display: bool = False


def _escaped(text: str, index: int) -> bool:
    count = 0
    while index > 0 and text[index - 1] == "\\":
        count += 1
        index -= 1
    return bool(count % 2)


def _delimiter_at(text: str, index: int, delimiter: str) -> bool:
    if not text.startswith(delimiter, index) or _escaped(text, index):
        return False
    if delimiter.startswith("$"):
        # A dollar run is indivisible: '$$' must never close a '$' token.
        return ((index == 0 or text[index - 1] != "$")
                and (index + len(delimiter) == len(text)
                     or text[index + len(delimiter)] != "$"))
    return True


def tokenize_math(text: str) -> list[Token]:
    """Scan matching delimiters without altering literal pipes or broken math.

    Dollar runs match exactly and escaped delimiters stay literal. An unmatched
    opener is retained as text; a later, independently valid formula may render.
    Every token's ``raw`` value concatenates back to the original input.
    """
    tokens: list[Token] = []
    cursor = plain_start = 0
    while cursor < len(text):
        matched = False
        for opening, closing, display in _DELIMITERS:
            if not _delimiter_at(text, cursor, opening):
                continue
            end = cursor + len(opening)
            while end < len(text) and not _delimiter_at(text, end, closing):
                end += 1
            if end < len(text):
                if cursor > plain_start:
                    literal = text[plain_start:cursor]
                    tokens.append(Token("text", literal, literal))
                stop = end + len(closing)
                tokens.append(Token("math", text[cursor + len(opening):end],
                                    text[cursor:stop], display))
                cursor = plain_start = stop
                matched = True
            else:
                cursor += len(opening)
                matched = True
            break
        if not matched:
            cursor += 1
    if plain_start < len(text):
        literal = text[plain_start:]
        tokens.append(Token("text", literal, literal))
    return tokens


def _inline_lines(text: str) -> list[str]:
    lines = [""]
    for token in tokenize_math(text):
        if token.kind == "math":
            # Never interpolate problem data into JavaScript, HTML or CSS.
            escaped = html.escape(token.text, quote=True)
            lines[-1] += (f'<span class="math" data-tex="{escaped}" '
                          f'data-display="{str(token.display).lower()}">{escaped}</span>')
        else:
            parts = token.text.split("\n")
            lines[-1] += html.escape(parts[0])
            lines.extend(html.escape(part) for part in parts[1:])
    return lines


def _prose(text: str) -> str:
    output: list[str] = []
    paragraph: list[str] = []
    list_kind = ""

    def flush() -> None:
        if paragraph:
            output.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = ""

    for line in _inline_lines(text):
        section = re.fullmatch(r"\s*(?:【(题面|输入|输出|数据范围|说明|样例说明)】|(题面|输入|输出|数据范围|说明|样例说明)[：:]?)\s*", line)
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        item = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)$", line)
        if not line.strip():
            flush()
            close_list()
        elif section:
            flush()
            close_list()
            output.append(f"<h2>{section[1] or section[2]}</h2>")
        elif heading:
            flush()
            close_list()
            level = len(heading[1])
            output.append(f"<h{level}>{heading[2]}</h{level}>")
        elif item:
            flush()
            kind = "ol" if re.match(r"^\s*\d", line) else "ul"
            if kind != list_kind:
                close_list()
                output.append(f"<{kind}>")
                list_kind = kind
            output.append(f"<li>{item[1]}</li>")
        else:
            close_list()
            paragraph.append(line)
    flush()
    close_list()
    return "\n".join(output)


def _markdown(text: str) -> str:
    output: list[str] = []
    prose: list[str] = []
    code: list[str] = []
    fence = ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if not fence and marker:
            output.append(_prose("".join(prose)))
            prose.clear()
            fence = marker[1]
        elif fence:
            if (marker and marker[1][0] == fence[0]
                    and len(marker[1]) >= len(fence) and not marker[2].strip()):
                output.append("<pre><code>" + html.escape("".join(code)) + "</code></pre>")
                code.clear()
                fence = ""
            else:
                code.append(line)
        else:
            prose.append(line)
    if fence:
        output.append("<pre><code>" + html.escape("".join(code)) + "</code></pre>")
    output.append(_prose("".join(prose)))
    return "\n".join(output)


_STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; background: #ffffff; color: #202124; }
body { padding: 30px; font: 22px/1.75 Arial, 'Noto Sans CJK SC', 'Noto Sans SC',
       'Microsoft YaHei', sans-serif; font-style: normal; overflow-wrap: anywhere; }
.card { border-left: 3px solid #62866a; padding: 4px 30px 20px; }
h1,h2,h3,h4,h5,h6 { color: #487052; font-size: 19px; font-weight: 600;
                    margin: 28px 0 12px; }
.card > h2:first-child { margin-top: 0; }
p { margin: 14px 0; } ul,ol { padding-left: 30px; } li { margin: 8px 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f6f8;
      border: 1px solid #e0e3e7; border-radius: 5px; padding: 18px 22px;
      font: 19px/1.7 'DejaVu Sans Mono', 'Noto Sans CJK SC', monospace; }
.math { white-space: pre-wrap; font-family: 'DejaVu Sans Mono', monospace; }
.math-fallback { white-space: pre-wrap; }
.limits { font-size: 17px; color: #42645a; margin: 0 0 18px; }
footer { color: #8a867c; font-size: 16px; margin-top: 28px; }
.statement-image { display: block; max-width: 100%; height: auto; margin: 18px auto; object-fit: contain; }
.image-warning { color: #a14d13; }
.katex { font-size: 1.12em; }
.katex-display { overflow-wrap: anywhere; margin: 18px 0; }
"""
_MATH_SCRIPT = """
for (const element of document.querySelectorAll('[data-tex]')) {
  try {
    katex.render(element.getAttribute('data-tex'), element, {
      displayMode: element.dataset.display === 'true',
      throwOnError: true, strict: 'ignore', trust: false,
      maxExpand: 1000, maxSize: 20
    });
    if (element.querySelector('.katex-error')) {
      element.textContent = element.getAttribute('data-tex');
      element.classList.add('math-fallback');
    }
  } catch (_) {
    element.textContent = element.getAttribute('data-tex');
    element.classList.add('math-fallback');
  }
}
window.__kouhuRendered = true;
"""


def build_html(problem: dict, examples: bool = False, width: int = 900) -> str:
    """Build safe HTML without requiring a browser or installed KaTeX assets.

    Only statement/sample fields are displayed. All source HTML stays literal.
    Local KaTeX CSS resolves its fonts relative to the asset directory.
    """
    _validate_width(width)
    assets = ASSET_DIR.resolve()
    css_url = html.escape((assets / "katex.min.css").as_uri(), quote=True)
    js_url = html.escape((assets / "katex.min.js").as_uri(), quote=True)
    pitfall = problem.get('kind') == 'pitfall'
    statement=str(problem.get('statement_cn') or problem.get('statement_en') or '')
    # Also handles previously cached combined statements without a cache migration.
    split=re.split(r'(?m)^\s*(?:【(?:说明|样例说明)】|#{1,6}\s*(?:说明|样例说明)|(?:说明|样例说明)[：:])\s*\n',statement,maxsplit=1)
    statement_body=split[0]
    note=split[1] if len(split)>1 else ''
    if pitfall:
        title='C++ 坑点练习'
        body='<h2>C++ 坑点练习</h2>'+_prose(str(problem.get('pitfall_question') or '找出代码的问题。'))
        body+='<pre><code>'+html.escape(str(problem.get('pitfall_code') or ''))+'</code></pre>'
        body+='<p>提交分析：grm sub 你的分析<br>查看答案：grm giveup</p>'
    elif examples:
        title = "样例"
        sections = []
        for index, example in enumerate(problem.get("examples") or [], 1):
            for key, label in (("input", "输入"), ("output", "输出")):
                value = html.escape(str(example.get(key) or ""))
                sections.append(f"<h2>样例 {index} · {label}</h2><pre><code>{value}</code></pre>")
        body = "\n".join(sections) or "<p>（这道题没有提供样例）</p>"
        if note.strip():
            body += '<h2>样例说明</h2>'+_markdown(note)
    else:
        title = "题面"
        text = statement_body or '（这道题没有题面内容）'
        body = _markdown(text)
        if not body.startswith("<h2>题面</h2>"):
            body = "<h2>题面</h2>" + body
    limits = '<p class="limits">时间限制：' + html.escape(str(problem.get('time_limit') or '未提供')) + (' 秒' if problem.get('time_limit') else '') + ' · 内存限制：' + html.escape(str(problem.get('memory_limit') or '未提供')) + (' MB' if problem.get('memory_limit') else '') + '</p>'
    if not pitfall:
        # Images are downloaded and normalized by the source; Chromium never
        # contacts CF. Substitute only opaque markers and validated PNG data.
        for image in problem.get('statement_images') or []:
            token=str(image.get('token') or '')
            source=str(image.get('src') or '')
            if re.fullmatch(r'KOUHUIMAGE\d+END',token):
                if re.fullmatch(r'data:image/png;base64,[A-Za-z0-9+/=]+',source):
                    replacement='<img class="statement-image" alt="题面示意图" src="'+source+'">'
                else:
                    replacement='<span class="image-warning">〔题面图片未获取，请查看原题链接〕</span>'
                body=body.replace(token,replacement)
    if pitfall:
        limits='<p class="limits">'+html.escape(str(problem.get('cpp_standard') or 'C++17'))+' · 找出问题并解释原因</p>'
    body = limits + body
    return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "script-src file: 'unsafe-inline'; style-src file: 'unsafe-inline'; font-src file: data:; "
            "img-src data:; connect-src 'none'\">"
            f'<title>{title}</title><link rel="stylesheet" href="{css_url}">'
            f'<style>{_STYLE} body {{ width: {width}px; }}</style></head><body>'
            f'<main class="card">{body}<footer>口胡判题 · 题面版权归原平台，仅供练习</footer></main>'
            f'<script src="{js_url}"></script><script>{_MATH_SCRIPT}</script></body></html>')


def _validate_width(width: int) -> None:
    if not isinstance(width, int) or isinstance(width, bool) or not 320 <= width <= 4096:
        raise ValueError("width must be an integer between 320 and 4096")


class RenderError(RuntimeError):
    """A statement could not be rendered as one bounded image."""


def _browser_executable(chromium) -> str | None:
    """Find this Playwright revision in the default, venv-local or image install."""
    expected = Path(chromium.executable_path)
    if expected.is_file():
        return str(expected)
    # executable_path identifies the compatible revision; never pick an
    # arbitrary installed browser or depend on the shell's working directory.
    tail = expected.parts[-3:]
    if not tail or not tail[0].startswith('chromium-'):
        return None
    roots = [Path(sys.prefix).parent / 'browsers', Path('/opt/kouhu/browsers')]
    roots.extend(parent / '.local' / 'browsers' for parent in Path(__file__).resolve().parents)
    for root in roots:
        candidate = root.joinpath(*tail)
        if candidate.is_file():
            return str(candidate)
    return None


def _render(problem: dict, out_dir: str | None, width: int, examples: bool) -> str:
    # The lock covers document creation, browser startup, screenshot and cleanup.
    with _RENDER_LOCK:
        _validate_width(width)
        for filename in ("katex.min.js", "katex.min.css"):
            if not (ASSET_DIR / filename).is_file():
                raise RenderError(f"Local KaTeX asset is unavailable: {ASSET_DIR / filename}")
        document = build_html(problem, examples=examples, width=width)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RenderError("Playwright is required for browser rendering") from exc
        directory = Path(out_dir or tempfile.gettempdir()).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"kouhu_{uuid.uuid4().hex}.png"
        with tempfile.TemporaryDirectory(prefix="kouhu-render-") as temporary:
            page_path = Path(temporary) / "statement.html"
            page_path.write_text(document, encoding="utf-8")
            with sync_playwright() as playwright:
                executable = _browser_executable(playwright.chromium)
                launch_options = {'executable_path':executable} if executable else {}
                browser = playwright.chromium.launch(headless=True, **launch_options, args=[
                    "--disable-dev-shm-usage", "--no-sandbox", "--renderer-process-limit=1",
                ])
                try:
                    context = browser.new_context(viewport={"width": width, "height": 1},
                                                  device_scale_factor=1, service_workers="block")
                    def route_request(route) -> None:
                        url = urlsplit(route.request.url)
                        local = Path(unquote(url.path)).resolve()
                        if url.scheme == "file" and (local == page_path or local.is_relative_to(ASSET_DIR.resolve())):
                            route.continue_()
                        else:
                            route.abort()
                    context.route("**/*", route_request)
                    page = context.new_page()
                    page.set_default_timeout(30_000)
                    page.goto(page_path.as_uri(), wait_until="load")
                    page.wait_for_function("window.__kouhuRendered === true")
                    page.evaluate("async () => { await document.fonts.ready; await Promise.all(Array.from(document.images, image => image.decode())); }")
                    dimensions = page.evaluate("""() => ({
                      height: Math.ceil(Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)),
                      width: Math.ceil(Math.max(document.body.scrollWidth, document.documentElement.scrollWidth))
                    })""")
                    if max(dimensions.values()) > MAX_HEIGHT:
                        raise RenderError(f"题面图片尺寸 {dimensions['width']}×{dimensions['height']}px 超过单边 {MAX_HEIGHT}px 上限")
                    page.screenshot(path=str(output), full_page=True, type="png")
                finally:
                    browser.close()
        return str(output)


def render_statement(problem: dict, out_dir: str | None = None, width: int = 900) -> str:
    """Return one PNG path, running the sync browser API on its dedicated thread."""
    return _RENDERER.submit(_render, problem, out_dir, width, False).result()


def render_examples(problem: dict, out_dir: str | None = None, width: int = 900) -> str:
    """Return one PNG containing escaped sample input/output, without math parsing."""
    return _RENDERER.submit(_render, problem, out_dir, width, True).result()
