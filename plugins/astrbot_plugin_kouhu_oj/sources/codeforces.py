"""Codeforces 题源：拉题目列表、抓题面、翻译、本地缓存。

CF 的接口（/api/problemset/problems）能直连，但题面页面挂在 Cloudflare 后面，
普通请求一律 403。这里用 curl_cffi 复刻 Chrome 的 TLS 指纹过掉挑战，
并且**把抓到的题面永久存进本地库**——哪天反爬再升级，
已经屯下来的题照样能派。

所有网络调用都走线程池，不阻塞 AstrBot 的事件循环。
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from astrbot.api import logger
from html.parser import HTMLParser
from urllib.parse import urljoin

from .statement_images import download_images

_IMAGE_TOKEN = re.compile(r"KOUHUIMAGE\d+END")


class _ImageTag(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.src = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            self.src = dict(attrs).get("src") or ""

# 插件在 AstrBot 里是以包（data.plugins.astrbot_plugin_kouhu_oj）加载的，
# 兄弟模块只能用相对导入；本地测试时 main.py 是顶层模块，才需要绝对兜底。
try:
    from .. import scoring
except ImportError:  # pragma: no cover - 仅本地测试走这条
    import scoring

API_URL = "https://codeforces.com/api/problemset.problems"
PROBLEM_URL = "https://codeforces.com/problemset/problem/{contest}/{index}"

# CF 用 $$$...$$$ 包 LaTeX，和标准 $...$ 不同
_TEX_BLOCK = re.compile(r"(?<!\$)(\${6}(?!\$).+?(?<!\$)\${6}(?!\$)|\${3}(?!\$).+?(?<!\$)\${3}(?!\$))", re.S)


# ----------------------------------------------------------------------
# HTML 解析
# ----------------------------------------------------------------------
def _extract_div(html: str, start: int) -> str:
    """从 html[start:] 里的第一个 <div 开始，返回它内部的 HTML（含嵌套）。"""
    open_tag = html.find("<div", start)
    if open_tag < 0:
        return ""
    tag_end = html.find(">", open_tag)
    if tag_end < 0:
        return ""
    content_start = tag_end + 1
    pos = content_start
    depth = 1
    while depth > 0:
        nxt_open = html.find("<div", pos)
        nxt_close = html.find("</div>", pos)
        if nxt_close < 0:
            return ""
        if 0 <= nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            if depth == 0:
                # 内容终点是让 depth 归零的那个 </div>，不是当前扫描位置
                return html[content_start:nxt_close]
            pos = nxt_close + 6
    return ""


def _pre_text(fragment: str) -> str:
    """Preserve CF's modern div-per-line samples as well as legacy br lines."""
    text=re.sub(r'<br\s*/?>','\n',fragment,flags=re.I)
    text=re.sub(r'</div\s*>','\n',text,flags=re.I)
    text=html_mod.unescape(re.sub(r'<[^>]+>','',text))
    return text.replace('\r\n','\n').strip('\n\r')


class _LegacyMathParser(HTMLParser):
    """Preserve legacy CF math spans and HTML indices before removing markup."""

    _INDEX_CLASSES = {'upper-index': '^', 'lower-index': '_', 'sup': '^', 'sub': '_'}
    _BLOCKS = {'p', 'div', 'li', 'ul', 'ol', 'br', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.math = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag in {'br', 'hr', 'wbr', 'img'}:
            if self.stack:
                self.math.append(' ')
            elif tag == 'br':
                self.parts.append(self.get_starttag_text())
            return
        classes = set((dict(attrs).get('class') or '').split())
        index = '^' if tag == 'sup' else '_' if tag == 'sub' else next(
            (marker for cls, marker in self._INDEX_CLASSES.items() if tag == 'span' and cls in classes), None)
        root = not self.stack
        if not root or (tag == 'span' and 'tex-span' in classes) or index:
            if root:
                self.math = []
                if index:
                    # An index outside tex-span must retain its base in the same formula.
                    preceding = ''.join(self.parts)
                    base = re.search(r'(\$\$\$([^$]+)\$\$\$|[A-Za-z0-9]+(?:\.[0-9]+)?)$', preceding)
                    if base:
                        self.parts = [preceding[:base.start()]]
                        self.math.append(base.group(2) if base.group(2) is not None else base.group(0))
            if index:
                self.math.append(index + '{')
            self.stack.append((tag, bool(index)))
        elif tag in self._BLOCKS:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if self.stack:
            if tag == 'br':
                self.math.append(' ')
        elif tag in self._BLOCKS:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.stack:
            if self.stack[-1][0] != tag:
                return
            _, index = self.stack.pop()
            if index:
                self.math.append('}')
            if not self.stack:
                tex = ''.join(self.math).strip()
                # Use TeX operators while leaving literal prose entities untouched.
                tex = tex.replace('≤', r'\leq ').replace('≥', r'\geq ').replace('−', '-')
                if re.fullmatch(r'KOUHUEXISTINGTEX\d+END', tex):
                    self.parts.append(tex)
                else:
                    self.parts.append(html_mod.escape('$$$' + tex + '$$$', quote=False))
        elif tag in self._BLOCKS:
            self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        if self.stack:
            self.math.append(data)
        else:
            self.parts.append(html_mod.escape(data, quote=False))

    def handle_entityref(self, name):
        entity = '&' + name + ';'
        if self.stack:
            self.math.append(html_mod.unescape(entity))
        else:
            self.parts.append(entity)

    def handle_charref(self, name):
        self.handle_entityref('#' + name)


def _text_of(fragment: str, image_urls: list[str] | None = None) -> str:
    """把 HTML 转成文本，图片原位替换为不可变 token。"""
    if image_urls is None:
        image_urls = []

    def save_image(match):
        parser = _ImageTag()
        parser.feed(match.group(0))
        source = parser.src
        # CF uses both absolute and site-relative image paths.
        source = urljoin("https://codeforces.com/", source) if source else ""
        token = f"KOUHUIMAGE{len(image_urls)}END"
        image_urls.append(source)
        return token

    fragment = re.sub(r'''<img\b(?:[^>"']|"[^"]*"|'[^']*')*>''', save_image, fragment, flags=re.I)
    code_blocks = []
    def save_pre(match):
        code = _pre_text(match.group(1))
        code_blocks.append(code)
        return '\n\nKOUHUPREBLOCK' + str(len(code_blocks)-1) + '\n\n'
    text = re.sub(r'<pre[^>]*>([\s\S]*?)</pre>', save_pre, fragment, flags=re.I)
    # Existing TeX is already semantic math, so never reinterpret its angle brackets.
    formulas = []
    def save_formula(match):
        formulas.append(match.group(0))
        return 'KOUHUEXISTINGTEX' + str(len(formulas) - 1) + 'END'
    text = _TEX_BLOCK.sub(save_formula, text)
    parser = _LegacyMathParser()
    parser.feed(text)
    parser.close()
    text = ''.join(parser.parts)
    # 块级元素变成换行
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "· ", text, flags=re.I)
    text = re.sub(r"</(div|ul|ol|pre|h\d)\s*>", "\n", text, flags=re.I)
    # 其余标签直接剥掉
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = text.replace("\xa0", " ")
    # 收拢空白：段落间最多空一行
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            out.append(ln.strip())
            blank = 0
        else:
            blank += 1
            if blank <= 1 and out:
                out.append("")
    result = '\n'.join(out).strip()
    for index, formula in enumerate(formulas):
        result = result.replace('KOUHUEXISTINGTEX' + str(index) + 'END', html_mod.unescape(formula))
    for index, code in enumerate(code_blocks):
        result = result.replace('KOUHUPREBLOCK' + str(index), '```\n' + code + '\n```')
    return result


def _strip_section_title(text: str) -> str:
    """section 的 div 里带着 <div class="section-title">Input</div>，
    这部分标题我们自己会用中文重写，先去掉。"""
    return re.sub(r"^(Input|Output|Note|Examples?)\s*\n?", "", text.strip(), flags=re.I).strip()


def _section(html: str, cls: str, image_urls: list[str] | None = None) -> str:
    """取出 class="cls" 的 div 的纯文本，取不到返回空串。"""
    marker = re.search(rf'<div class="{re.escape(cls)}"', html)
    if not marker:
        return ""
    return _text_of(_extract_div(html, marker.start()), image_urls)


def _top_level_divs(inner: str) -> list[tuple[str, str]]:
    """把 problem-statement 的直接子 div 拆出来，返回 [(class, 内 HTML)]。"""
    result: list[tuple[str, str]] = []
    pos = 0
    while True:
        m = re.search(r"<div([^>]*)>", inner[pos:])
        if not m:
            break
        start = pos + m.start()
        cls_match = re.search(r'class="([^"]*)"', m.group(1))
        cls = cls_match.group(1) if cls_match else ""
        content = _extract_div(inner, start)
        result.append((cls, content))
        # 跳过这个 div 的整体
        pos = start + len(m.group(1)) + 5 + len(content) + 6
        if pos >= len(inner):
            break
    return result


def parse_statement(html: str) -> dict[str, Any]:
    """从题面页面里抽出题面、样例、时限、内存。"""
    start = html.find('<div class="problem-statement">')
    if start < 0:
        return {}
    inner = _extract_div(html, start)

    title = ""
    m = re.search(r'<div class="title">([\s\S]*?)</div>', inner)
    if m:
        title = _text_of(m.group(1))

    time_limit = 0.0
    memory_limit = 0
    for cls in ("time-limit", "memory-limit"):
        marker = re.search(rf'<div class="{cls}">', html)
        if not marker:
            continue
        # 这个 div 里还嵌着 property-title 的 </div>，必须用配平取内容
        body = _text_of(_extract_div(html, marker.start()))
        if cls == "time-limit":
            m = re.search(r"([\d.]+)\s*second", body)
            if m:
                time_limit = float(m.group(1))
        else:
            m = re.search(r"(\d+)\s*megabyte", body)
            if m:
                memory_limit = int(m.group(1))

    # 正文和各节共用索引，图片在 statement_en 中保持原始顺序。
    image_urls: list[str] = []
    # 正文 = 第一个没有 class 的子 div
    body = ""
    for cls, content in _top_level_divs(inner):
        if cls == "":
            body = _text_of(content, image_urls)
            break

    input_spec = _strip_section_title(_section(inner, "input-specification", image_urls))
    output_spec = _strip_section_title(_section(inner, "output-specification", image_urls))
    note = _strip_section_title(_section(inner, "note", image_urls))

    examples: list[dict[str, str]] = []
    sample_start = inner.find('<div class="sample-tests">')
    if sample_start >= 0:
        sample = _extract_div(inner, sample_start)
        marks: list[tuple[int, str]] = []
        for kind in ("input", "output"):
            for m in re.finditer(rf'<div class="{kind}">', sample):
                marks.append((m.start(), kind))
        marks.sort()
        blocks: list[tuple[str, str]] = []
        for start, kind in marks:
            content = _extract_div(sample, start)
            pre = re.search(r"<pre[^>]*>([\s\S]*?)</pre>", content)
            if pre:
                text = _pre_text(pre.group(1))
            else:
                text = _strip_section_title(_text_of(content))
            blocks.append((kind, text))
        for i in range(0, len(blocks) - 1, 2):
            if blocks[i][0] == "input" and blocks[i + 1][0] == "output":
                examples.append({"input": blocks[i][1], "output": blocks[i + 1][1]})

    parts: list[str] = []
    if body:
        parts.append(body)
    if input_spec:
        parts.append("【输入】\n" + input_spec)
    if output_spec:
        parts.append("【输出】\n" + output_spec)
    if note:
        parts.append("【说明】\n" + note)

    return {
        "title": title,
        "statement_en": "\n\n".join(parts).strip(),
        "image_urls": image_urls,
        "examples": examples,
        "time_limit": time_limit,
        "memory_limit": memory_limit,
    }


# ----------------------------------------------------------------------
# 公式处理
# ----------------------------------------------------------------------
_MACRO_FIXES = (
    (r"\\le\b", r"\\leq"),
    (r"\\ge\b", r"\\geq"),
    (r"\\lt\b", "<"),
    (r"\\gt\b", ">"),
    (r"\\dots\b", r"\\ldots"),
    (r"\\text\{([^{}]*)\}", r"\\mathrm{\1}"),
    (r"\\texttt\{([^{}]*)\}", r"\\mathtt{\1}"),
    (r"\\cdot\b", r"\\cdot "),
    # 注意：CF 用 ~ 表示数学空格，mathtext 本身认得 ~，不能替换成普通空格
)


def normalize_tex(tex: str) -> str:
    """把 CF 的 LaTeX 方言修成 matplotlib mathtext 认得的写法。"""
    out = tex
    for pattern, repl in _MACRO_FIXES:
        out = re.sub(pattern, repl, out)
    return out.strip()


def protect_tex(text: str) -> tuple[str, list[str]]:
    """把 $$$...$$$ 换成占位符，供翻译时保护公式不被改动。"""
    found: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        found.append(m.group(1))
        return f"⟦{len(found) - 1}⟧"

    return _TEX_BLOCK.sub(_sub, text), found


def restore_tex(text: str, found: list[str]) -> str:
    """把占位符换回公式原文。

    模型可能会把 ⟦0⟧ 写成 [0]、【0】、[[0]] 等，这几种都认。
    """
    def _sub(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return found[idx] if 0 <= idx < len(found) else m.group(0)

    return re.sub(r"[⟦\[［【]+\s*(\d+)\s*[⟧\]］】]+", _sub, text)


def translation_incomplete(text: str) -> bool:
    """长英文叙述残留意味着整段未译；变量及短英文名称不误报。"""
    plain = _TEX_BLOCK.sub('', text)
    return bool(re.search(r'(?:[A-Za-z]+[ ,;:\-]+){12,}[A-Za-z]+', plain))


def tex_intact(restored: str, expected: int) -> bool:
    """检查译文里公式数量对不对得上，对不上说明模型改坏了。"""
    return len(_TEX_BLOCK.findall(restored)) == expected


def strip_tex(text: str, render: Callable[[str], str] | None = None) -> str:
    """把 $$$...$$$ 换成可读形式。

    render 给了就用它（渲染成图片占位），没给就退化成普通文本。
    """
    def _sub(m: re.Match[str]) -> str:
        raw = m.group(1)
        delimiter = 6 if raw.startswith('$$$$$$') else 3
        tex = raw[delimiter:-delimiter]
        if render is not None:
            return render(tex)
        return tex.strip()

    return _TEX_BLOCK.sub(_sub, text)


# ----------------------------------------------------------------------
# 缓存库
# ----------------------------------------------------------------------
_SAMPLE_PARSER_VERSION = 2

_DDL_CATALOG = """
    CREATE TABLE IF NOT EXISTS cf_catalog (
        id         INTEGER PRIMARY KEY CHECK (id = 1),
        payload    TEXT NOT NULL,
        updated_at REAL NOT NULL
    )
"""

_DDL = """
    CREATE TABLE IF NOT EXISTS cf_problems (
        cf_key        TEXT PRIMARY KEY,
        contest_id    INTEGER,
        idx           TEXT,
        rating        INTEGER,
        tier          INTEGER,
        points        INTEGER,
        title         TEXT,
        statement_en  TEXT,
        statement_cn  TEXT,
        examples_json TEXT,
        sample_parser_version INTEGER DEFAULT 1,
        images_json   TEXT,
        time_limit    REAL,
        memory_limit  INTEGER,
        fetched_at    REAL,
        translated_at REAL,
        used_count    INTEGER DEFAULT 0
    )
"""


class CfCache:
    """题面缓存。和积分库分开存，升级表结构时不会被连带清空。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_DDL)
        self._conn.execute(_DDL_CATALOG)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(cf_problems)")}
        if "images_json" not in columns:
            self._conn.execute("ALTER TABLE cf_problems ADD COLUMN images_json TEXT")
        if "sample_parser_version" not in columns:
            self._conn.execute(
                "ALTER TABLE cf_problems ADD COLUMN sample_parser_version INTEGER DEFAULT 1"
            )
        if "statement_parser_version" not in columns:
            self._conn.execute("ALTER TABLE cf_problems ADD COLUMN statement_parser_version INTEGER DEFAULT 1")
        self._conn.commit()

    def get(self, cf_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM cf_problems WHERE cf_key = ?", (cf_key,)
        ).fetchone()
        return dict(row) if row else None

    def save(self, problem: dict) -> None:
        self._conn.execute(
            """
            INSERT INTO cf_problems (
                cf_key, contest_id, idx, rating, tier, points, title,
                statement_en, statement_cn, examples_json, images_json,
                time_limit, memory_limit, fetched_at, translated_at, used_count,
                sample_parser_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cf_key) DO UPDATE SET
                rating = excluded.rating,
                tier = excluded.tier,
                points = excluded.points,
                title = excluded.title,
                statement_en = COALESCE(excluded.statement_en, cf_problems.statement_en),
                statement_cn = CASE
                    WHEN excluded.statement_en IS NOT NULL
                         AND excluded.statement_en IS NOT cf_problems.statement_en
                    THEN excluded.statement_cn
                    ELSE COALESCE(excluded.statement_cn, cf_problems.statement_cn) END,
                examples_json = COALESCE(excluded.examples_json, cf_problems.examples_json),
                images_json = COALESCE(excluded.images_json, cf_problems.images_json),
                time_limit = COALESCE(excluded.time_limit, cf_problems.time_limit),
                memory_limit = COALESCE(excluded.memory_limit, cf_problems.memory_limit),
                fetched_at = excluded.fetched_at,
                translated_at = COALESCE(excluded.translated_at, cf_problems.translated_at),
                sample_parser_version = COALESCE(?, cf_problems.sample_parser_version)
            """,
            (
                problem["cf_key"], problem.get("contest_id"), problem.get("idx"),
                problem.get("rating"), problem.get("tier"), problem.get("points"),
                problem.get("title"), problem.get("statement_en"),
                problem.get("statement_cn"),
                (json.dumps(problem["examples"] or [], ensure_ascii=False)
                 if "examples" in problem else problem.get("examples_json")),
                (json.dumps(problem["statement_images"], ensure_ascii=False)
                 if "statement_images" in problem else problem.get("images_json")),
                problem.get("time_limit"), problem.get("memory_limit"),
                problem.get("fetched_at") or time.time(),
                problem.get("translated_at"),
                0,
                problem.get("sample_parser_version", 1),
                problem.get("sample_parser_version"),
            ),
        )
        self._conn.execute('UPDATE cf_problems SET statement_parser_version=? WHERE cf_key=?',
                           (problem.get('statement_parser_version', 2), problem['cf_key']))
        self._conn.commit()

    def save_samples(self, cf_key: str, examples: list[dict[str, str]]) -> None:
        """只升级样例，不改题面、翻译、图片或使用记录。"""
        self._conn.execute(
            "UPDATE cf_problems SET examples_json = ?, sample_parser_version = ? "
            "WHERE cf_key = ?",
            (json.dumps(examples, ensure_ascii=False), _SAMPLE_PARSER_VERSION, cf_key),
        )
        self._conn.commit()

    def ready_count(self, tier: int | None = None) -> int:
        """可用题数（已抓到题面且已翻译）。"""
        sql = ("SELECT COUNT(*) FROM cf_problems "
               "WHERE statement_en IS NOT NULL AND statement_cn IS NOT NULL")
        args: list[Any] = []
        if tier is not None:
            sql += " AND tier = ?"
            args.append(tier)
        return int(self._conn.execute(sql, args).fetchone()[0])

    def load_catalog(self) -> tuple[list[dict], float]:
        """读出上次的题目总表；重启后即使 CF 暂时不可用也能按标签抽题。"""
        row = self._conn.execute(
            "SELECT payload, updated_at FROM cf_catalog WHERE id = 1"
        ).fetchone()
        if not row:
            return [], 0.0
        try:
            data = json.loads(row["payload"])
        except (TypeError, ValueError):
            return [], 0.0
        if not isinstance(data, list):
            return [], 0.0
        return data, float(row["updated_at"] or 0.0)

    def save_catalog(self, data: list[dict]) -> None:
        self._conn.execute(
            "INSERT INTO cf_catalog (id, payload, updated_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
            "updated_at = excluded.updated_at",
            (json.dumps(data, ensure_ascii=False), time.time()),
        )
        self._conn.commit()

    def known_keys(self) -> set[str]:
        rows = self._conn.execute("SELECT cf_key FROM cf_problems").fetchall()
        return {r["cf_key"] for r in rows}

    def bump_used(self, cf_key: str) -> None:
        self._conn.execute(
            "UPDATE cf_problems SET used_count = COALESCE(used_count,0) + 1 WHERE cf_key = ?",
            (cf_key,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ----------------------------------------------------------------------
# 题源
# ----------------------------------------------------------------------
class CodeforcesSource:
    """CF 题源。抓题面走线程池，翻译交给外部传入的协程。"""

    def __init__(
        self,
        cache: CfCache,
        translate: Callable[[str], Awaitable[str]] | None = None,
        timeout: float = 40.0,
    ) -> None:
        self._cache = cache
        self._translate = translate
        self._timeout = timeout
        self._catalog: list[dict] = []
        self._catalog_at = 0.0
        try:
            cached, cached_at = cache.load_catalog()
            data = list(cached) if cached else []
            if data:
                self._catalog = data
                self._catalog_at = cached_at
                logger.info(f"[KouHuOJ] 已载入本地CF题目总表，共 {len(data)} 道")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[KouHuOJ] 读取本地CF题目总表失败：{type(exc).__name__}")
        self._scraper = None
        self._lock = asyncio.Lock()

    # -- 题目列表 ------------------------------------------------------
    async def refresh_catalog(self, force: bool = False) -> int:
        """拉 CF 的题目总表，缓存在内存里。"""
        if not force and self._catalog and time.time() - self._catalog_at < 6 * 3600:
            return len(self._catalog)
        async with self._lock:
            if not force and self._catalog and time.time() - self._catalog_at < 6 * 3600:
                return len(self._catalog)
            data = await asyncio.to_thread(self._fetch_catalog_sync)
            self._catalog = data
            self._catalog_at = time.time()
            try:
                self._cache.save_catalog(data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[KouHuOJ] 保存CF题目总表失败：{type(exc).__name__}")
            logger.info(f"[KouHuOJ] CF 题目总表已更新，共 {len(data)} 道")
            return len(data)

    def _fetch_catalog_sync(self) -> list[dict]:
        # 题目总表这个 API 本身不在 Cloudflare 挑战后面，但统一用 curl_cffi
        # 可以少带一个 HTTP 栈（httpx 是 AstrBot 自带的，行为随版本漂）。
        from curl_cffi import requests as curl_requests

        resp = curl_requests.get(API_URL, impersonate="chrome", timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "OK":
            raise RuntimeError(f"CF 接口返回异常：{payload.get('comment')}")
        problems = payload["result"].get("problems") or []
        out: list[dict] = []
        for p in problems:
            # Quantum/Q# output-only tasks are not ordinary algorithm solutions.
            if '*special' in (p.get('tags') or []) or p.get('type','PROGRAMMING')!='PROGRAMMING':
                continue
            rating = p.get("rating")
            if not rating:
                continue          # 没标难度的题不参与派题
            cid = p.get("contestId")
            idx = p.get("index")
            if cid is None or not idx:
                continue
            out.append({
                "cf_key": f"{cid}{idx}",
                "contest_id": cid,
                "idx": idx,
                "name": p.get("name") or "",
                "tags": sorted(set(p.get('tags') or [])),
                "rating": int(rating),
                "tier": scoring.tier_of(rating),
            })
        # 正常总表有一万多道；数量骤降说明接口返回不完整或字段变了，
        # 宁可保留旧目录，也不要用残缺数据把标签列表清空。
        if len(problems) > 1000:
            if len(out) < 500:
                raise RuntimeError(f"CF 题目总表异常：{len(problems)} 道原始数据只解析出 {len(out)} 道")
            tagged = sum(1 for p in out if p["tags"])
            if tagged < len(out) * 0.5:
                raise RuntimeError("CF 题目总表标签字段异常，暂不替换本地目录")
        if not out:
            raise RuntimeError("CF 题目总表为空")
        return out

    def pick(self, tier: int, exclude: set[str] | None = None) -> dict | None:
        """指定档位按唯一题号等概率抽样；new 不传历史排除集。"""
        exclude = exclude or set()
        target = scoring.tier_of(tier)
        import secrets
        pool = list({p['cf_key']: p for p in self._catalog
                     if p['tier'] == target and p['cf_key'] not in exclude}.values())
        return secrets.choice(pool) if pool else None

    def pick_range(self, low: int, high: int, exclude: set[str] | None = None) -> dict | None:
        """按实际 CF rating 闭区间抽题，不扩展区间或按档位取整。"""
        import secrets
        excluded = exclude or set()
        pool = list({p['cf_key']: p for p in self._catalog if isinstance(p.get('rating'), int)
                     and low <= p['rating'] <= high and p['cf_key'] not in excluded}.values())
        return secrets.choice(pool) if pool else None

    def catalog_size(self) -> int:
        return len(self._catalog)

    def catalog_ready(self) -> bool:
        return bool(self._catalog)

    def catalog_age(self) -> float:
        return max(0.0, time.time() - self._catalog_at) if self._catalog_at else 0.0

    def tag_counts_in_range(self, tags: list[str], low: int, high: int) -> dict[str, int]:
        """每个标签单独落在 rating 区间里的题数，用来解释“组合为空”。"""
        wanted = list(dict.fromkeys(tags))
        counts = {tag: 0 for tag in wanted}
        seen: set[str] = set()
        for problem in self._catalog:
            key = problem.get("cf_key")
            if not key or key in seen:
                continue
            seen.add(key)
            rating = problem.get("rating")
            if not isinstance(rating, int) or not low <= rating <= high:
                continue
            own = set(problem.get("tags") or [])
            for tag in wanted:
                if tag in own:
                    counts[tag] += 1
        return counts

    def tag_counts(self) -> dict[str, int]:
        counts={}
        for problem in {p['cf_key']:p for p in self._catalog}.values():
            for tag in set(problem.get('tags') or []):
                counts[tag]=counts.get(tag,0)+1
        return dict(sorted(counts.items()))

    def pick_tags(self, tags: list[str], low: int = 800, high: int = 3500) -> dict | None:
        """Match all requested tags and sample unique problem ids uniformly."""
        import secrets
        if not tags:
            raise ValueError("pick_tags 需要至少一个标签")
        required=set(tags)
        pool=list({p['cf_key']:p for p in self._catalog
                   if isinstance(p.get('rating'),int) and low<=p['rating']<=high
                   and required.issubset(p.get('tags') or [])}.values())
        return secrets.choice(pool) if pool else None

    # -- 题面 ----------------------------------------------------------
    def _scrape_sync(self, cf_key: str, contest_id: int, idx: str) -> dict:
        """抓一道题的题面页。

        为什么用 curl_cffi 而不是 requests + cloudscraper：
        CF 的题面页在 Cloudflare 的「Just a moment...」JS 挑战后面。
        cloudscraper 在开发机上能过，但在**部署用的容器里一律 403**——
        原因是挑战的难度取决于客户端的 TLS 指纹，容器里 OpenSSL 3.5 的握手
        比开发机 OpenSSL 3.0 更像脚本，CF 直接给了不可解的挑战。
        换成 host 网络跑也一样 403，所以不是网络层的问题。

        curl_cffi 用 BoringSSL 完整复刻 Chrome 的 TLS 指纹，容器里稳定 200。
        实测单题 0.6~0.9 秒。CF 的 robots.txt 是 Allow: /（只禁 AI 训练爬虫），
        抓题面本身是允许的。
        """
        from curl_cffi import requests as curl_requests

        if self._scraper is None:
            self._scraper = curl_requests.Session(impersonate="chrome")

        url = PROBLEM_URL.format(contest=contest_id, index=idx)
        resp = self._scraper.get(url, timeout=self._timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"抓取题面失败 HTTP {resp.status_code}（{cf_key}）")
        return parse_statement(resp.text)

    async def _repair_sample_row(self, cached: dict) -> dict:
        # Real SQLite rows always carry the marker after the additive migration.
        # Partial cache adapters/test doubles without it make no legacy-version claim.
        version = cached.get("sample_parser_version", _SAMPLE_PARSER_VERSION)
        if isinstance(version, int) and version >= _SAMPLE_PARSER_VERSION:
            return cached
        cf_key = cached["cf_key"]
        try:
            contest_id, idx = cached.get("contest_id"), cached.get("idx")
            if not contest_id or not idx:
                match = re.fullmatch(r"(\d+)([A-Za-z][A-Za-z0-9]*)", cf_key)
                if not match:
                    raise ValueError("无法从题号解析比赛和题目编号")
                contest_id, idx = int(match.group(1)), match.group(2)
            parsed = await asyncio.to_thread(self._scrape_sync, cf_key, contest_id, idx)
            examples = parsed.get("examples")
            # An empty/challenge page or incomplete parse must not erase old samples
            # or suppress a later repair attempt.
            if (not parsed.get("statement_en") or not isinstance(examples, list)
                    or not examples or not all(
                        isinstance(example, dict)
                        and isinstance(example.get("input"), str)
                        and isinstance(example.get("output"), str)
                        for example in examples)):
                raise ValueError("重新解析的题面或样例为空/不完整")
            self._cache.save_samples(cf_key, examples)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[KouHuOJ] {cf_key} 样例缓存升级失败，保留旧缓存：{exc}")
            return cached
        repaired = dict(cached)
        repaired.update(examples=examples, sample_parser_version=_SAMPLE_PARSER_VERSION)
        repaired["examples_json"] = json.dumps(examples, ensure_ascii=False)
        return repaired

    async def repair_samples(self, cf_key: str) -> dict | None:
        """升级旧样例并返回可渲染题目；失败返回原缓存，未缓存返回 None。

        供 cur 等直接读取当前题目的路径调用；不翻译题面、不重新下载图片。
        """
        cached = self._cache.get(cf_key)
        if not cached:
            return None
        return self._row_to_problem(await self._repair_sample_row(cached))

    async def fetch(self, entry: dict) -> dict:
        """拿到一道完整可用的题（题面 + 中文译文），优先用缓存。"""
        cf_key = entry["cf_key"]
        cached = self._cache.get(cf_key)
        if cached and cached.get('statement_parser_version', 2) < 2:
            # Old tag stripping destroyed indices; only a fresh source can recover them.
            cached = dict(cached)
            cached['statement_en'] = None
            cached['statement_cn'] = None
        if cached and cached.get("statement_en"):
            text = cached["statement_en"]
            try:
                images = json.loads(cached.get("images_json") or "[]")
                expected = [item["token"] for item in images if item.get("src")]
                metadata_ok = (isinstance(images, list) and expected == _IMAGE_TOKEN.findall(text)
                               and all(isinstance(item.get("src"), str)
                                       and (not item["src"] or item["src"].startswith("data:image/png;base64,"))
                                       for item in images))
            except (ValueError, TypeError, KeyError, AttributeError):
                metadata_ok = False
            if "〔图〕" in text or not metadata_ok:
                cached = None  # Legacy image placeholders require a fresh source scrape.
        if cached and cached.get("statement_en"):
            cached = await self._repair_sample_row(cached)
        if cached and cached.get("statement_en") and cached.get("statement_cn"):
            _, formulas = protect_tex(cached['statement_en'])
            translated_formulas = _TEX_BLOCK.findall(cached['statement_cn'])
            source_prose=_TEX_BLOCK.sub('',cached['statement_en'])
            translated_prose=_TEX_BLOCK.sub('',cached['statement_cn'])
            has_translation=(not re.search(r'[A-Za-z]{3,}',source_prose)
                             or bool(re.search(r'[\u3400-\u9fff]',translated_prose)))
            if (has_translation and translated_formulas == formulas and not translation_incomplete(cached['statement_cn'])
                    and _IMAGE_TOKEN.findall(cached['statement_cn']) == _IMAGE_TOKEN.findall(cached['statement_en'])):
                return self._row_to_problem(cached)
            cached = dict(cached)
            cached['statement_cn'] = None

        problem = dict(cached or {})
        problem.update({
            "cf_key": cf_key,
            "contest_id": entry["contest_id"],
            "idx": entry["idx"],
            "rating": entry.get("rating"),
            "tier": scoring.tier_of(entry.get("rating")),
        })
        problem["points"] = scoring.points_for(problem["tier"])

        if not problem.get("statement_en"):
            parsed = await asyncio.to_thread(
                self._scrape_sync, cf_key, entry["contest_id"], entry["idx"]
            )
            if not parsed.get("statement_en"):
                raise RuntimeError(f"题面解析为空（{cf_key}）")
            images = await asyncio.to_thread(download_images, parsed.get("image_urls") or [])
            statement = parsed["statement_en"]
            for image in images:
                if not image.get("src"):
                    statement = statement.replace(image["token"], image.get("error") or "〔图片加载失败〕")
            problem.update({
                "statement_images": images,
                "statement_cn": None,
                "title": parsed["title"] or entry.get("name") or cf_key,
                "statement_en": statement,
                "examples": parsed["examples"],
                "sample_parser_version": _SAMPLE_PARSER_VERSION,
                "statement_parser_version": 2,
                "time_limit": parsed["time_limit"],
                "memory_limit": parsed["memory_limit"],
                "fetched_at": time.time(),
            })
            self._cache.save(problem)

        if not problem.get("statement_cn"):
            if self._translate is None:
                raise RuntimeError('中文题面翻译未配置；英文原文已缓存，不占用当前题位')
            else:
                protected, tex = protect_tex(problem["statement_en"])
                translation_deadline=time.monotonic()+200
                timeout_retries=0
                async def translate_piece(text):
                    nonlocal timeout_retries
                    while True:
                        remaining=translation_deadline-time.monotonic()
                        if remaining<=0:
                            raise TimeoutError('整题翻译超过200秒预算')
                        try:
                            return await asyncio.wait_for(self._translate(text),timeout=remaining)
                        except (asyncio.TimeoutError,TimeoutError,ConnectionError):
                            if timeout_retries>=1 or translation_deadline-time.monotonic()<10:
                                raise
                            timeout_retries+=1
                            logger.info(f'[KouHuOJ] {cf_key} 翻译服务暂时失败，预算内重试一次')
                try:
                    for attempt in range(2):
                        # Image tokens never reach the translation model: translate
                        # the prose on either side and join the immutable markers back.
                        # Preserve paragraph context in one request; splitting every
                        # paragraph multiplied latency on the deployed cloud model.
                        paragraphs = re.split(r'(KOUHUIMAGE\d+END|〔图片加载失败[^〕]*〕)', protected)
                        translated_parts = []
                        for paragraph in paragraphs:
                            if (_IMAGE_TOKEN.fullmatch(paragraph) or paragraph.startswith('〔图片加载失败')
                                    or not re.search('[A-Za-z]', paragraph)):
                                translated_parts.append(paragraph)
                            else:
                                expected_tokens = re.findall(r'⟦\d+⟧',paragraph)
                                result = await translate_piece(paragraph)
                                actual_tokens = re.findall(r'[⟦\[［【]+\s*(\d+)\s*[⟧\]］】]+',result)
                                expected_ids = [re.search(r'\d+',x).group() for x in expected_tokens]
                                if (actual_tokens != expected_ids or translation_incomplete(result)
                                        or (re.search(r'[A-Za-z]{3,}',paragraph) and not re.search(r'[\u3400-\u9fff]',result))):
                                    # Keep formula placeholders in Python; translate only prose
                                    # fragments so a model cannot renumber/drop math tokens.
                                    pieces = re.split(r'(⟦\d+⟧)',paragraph)
                                    pending={str(i):piece for i,piece in enumerate(pieces)
                                             if piece and not re.fullmatch(r'⟦\d+⟧',piece) and re.search(r'[A-Za-z]{2}',piece)}
                                    recovered={}
                                    for fragment_attempt in range(2):
                                        if not pending:
                                            break
                                        raw=await translate_piece('KOUHU_TRANSLATION_BATCH\n'+json.dumps(pending,ensure_ascii=False))
                                        raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip())
                                        try:
                                            batch=json.loads(raw)
                                        except (ValueError,TypeError):
                                            continue
                                        if not isinstance(batch,dict) or set(batch)!=set(pending):
                                            continue
                                        for key,piece in list(pending.items()):
                                            fragment=batch[key]
                                            if (isinstance(fragment,str) and fragment.strip()
                                                    and not translation_incomplete(fragment)
                                                    and not re.search(r'⟦\s*\d+\s*⟧',fragment)
                                                    and (not re.search(r'[A-Za-z]{3,}',piece) or re.search(r'[\u3400-\u9fff]',fragment))):
                                                recovered[int(key)]=fragment
                                                del pending[key]
                                    if pending:
                                        raise ValueError('批量正文翻译未完成：编号缺失、正文未译或多余公式标记')
                                    result=''.join(recovered.get(i,piece) for i,piece in enumerate(pieces))
                                translated_parts.append(result)
                        translated = ''.join(translated_parts)
                        tokens = [int(x) for x in re.findall(r'[⟦\[［【]+\s*(\d+)\s*[⟧\]］】]+', translated)]
                        restored = restore_tex(translated, tex)
                        if (tokens == list(range(len(tex))) and _TEX_BLOCK.findall(restored) == tex
                                and not translation_incomplete(restored)
                                and _IMAGE_TOKEN.findall(restored) == _IMAGE_TOKEN.findall(problem['statement_en'])):
                            break
                    else:
                        raise ValueError('译文公式占位符丢失、重复、顺序错误或正文未译完整')
                except Exception as exc:  # noqa: BLE001
                    # Preserve source only; failed translations never become a
                    # Chinese cache entry or replace the group's current problem.
                    reason=f'{type(exc).__name__}: {exc}'
                    logger.warning(f"[KouHuOJ] {cf_key} 中文题面翻译未完成：{reason}")
                    raise RuntimeError('中文题面翻译未完成（'+reason+'）；英文原文已缓存，不占用当前题位') from exc
                restored = restore_tex(translated, tex)
                if not tex_intact(restored, len(tex)):
                    raise RuntimeError('中文题面翻译公式数量不符；保留英文缓存等待重试')
                problem["statement_cn"] = restored
                problem["translated_at"] = time.time()
                self._cache.save(problem)

        self._cache.bump_used(cf_key)
        return self._row_to_problem(problem)

    @staticmethod
    def _row_to_problem(row: dict) -> dict:
        examples = row.get("examples") or row.get("examples_json") or []
        if isinstance(examples, str):
            try:
                examples = json.loads(examples)
            except (ValueError, TypeError):
                examples = []
        images = row.get("statement_images") or row.get("images_json") or []
        if isinstance(images, str):
            try:
                images = json.loads(images)
            except (ValueError, TypeError):
                images = []
        return {
            "statement_images": images,
            "cf_key": row["cf_key"],
            "contest_id": row.get("contest_id"),
            "idx": row.get("idx"),
            "rating": row.get("rating"),
            "tier": row.get("tier") or scoring.tier_of(row.get("rating")),
            "points": row.get("points") or scoring.points_for(row.get("tier") or 1200),
            "title": row.get("title") or "",
            "statement_en": row.get("statement_en") or "",
            "statement_cn": row.get("statement_cn") or row.get("statement_en") or "",
            "examples": examples,
            "time_limit": row.get("time_limit") or 2.0,
            "memory_limit": row.get("memory_limit") or 256,
        }
