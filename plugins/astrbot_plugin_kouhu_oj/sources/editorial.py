"""Discover and retrieve the editorial linked by an official CF contest page.

No search or model inference is involved. Network dependencies are imported only
when fetching, so importing this module does not require AstrBot or curl_cffi.
"""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

MAX_HTTP_BYTES = 3 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
_CONTEST_URL = "https://codeforces.com/contest/{contest_id}"
_LABEL = re.compile(r"editorial|tutorial|题解|разбор", re.IGNORECASE)
_ENGLISH_LABEL = re.compile(r"editorial|tutorial", re.IGNORECASE)


def _blog_url(href: str, base: str) -> str | None:
    """Canonicalize only ordinary links to codeforces.com/blog/entry/<id>."""
    try:
        url = urlsplit(urljoin(base, href.strip()))
        if (url.scheme not in {"http", "https"}
                or url.hostname != "codeforces.com"
                or url.username is not None or url.password is not None
                or url.port not in {None, 80, 443}
                or not re.fullmatch(r"/blog/entry/[0-9]+/?", url.path)):
            return None
    except ValueError:
        return None
    return "https://codeforces.com" + url.path.rstrip("/")


class _ContestParser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links: list[tuple[int, str]] = []
        self._href: str | None = None
        self._label: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._ignored += 1
        if self._ignored:
            return
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._label = []

    def handle_data(self, data):
        if self._href is not None and not self._ignored:
            self._label.append(data)

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._ignored:
            self._ignored -= 1
            return
        if tag == "a" and self._href is not None and not self._ignored:
            label = " ".join(self._label)
            url = _blog_url(self._href, self.base)
            if url and _LABEL.search(label):
                self.links.append((0 if _ENGLISH_LABEL.search(label) else 1, url))
            self._href = None

    def editorial_url(self) -> str | None:
        # Stable ordering prefers an English editorial/tutorial when both language
        # versions are present; within each group retain the contest's ordering.
        return min(self.links, key=lambda item: item[0])[1] if self.links else None


class _EditorialParser(HTMLParser):
    _BLOCKS = {"p", "div", "section", "article", "blockquote", "ul", "ol",
               "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self._depth = 0
        self.complete = False
        self._ignored = 0
        self._parts: list[str] = []
        self._pre: list[str] | None = None
        self._pre_blocks: list[str] = []
        self._spans: list[int | None] = []

    def _append(self, value: str) -> None:
        (self._pre if self._pre is not None else self._parts).append(value)

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._ignored += 1
        if self._ignored:
            return
        if tag == "title":
            self._in_title = True
        classes = set((dict(attrs).get("class") or "").split())
        if not self._depth:
            if not self.complete and tag == "div" and "ttypography" in classes:
                self._depth = 1
            return
        if tag == "div":
            self._depth += 1
        if tag == "pre":
            self._parts.append("\n\n")
            self._pre = []
        elif tag == "br":
            self._append("\n")
        elif tag in self._BLOCKS:
            self._append("\n\n")
            if tag == "li":
                self._append("- ")
        elif tag in {"td", "th"}:
            self._append("\t")
        if tag == "span":
            self._spans.append(len(self._parts) if self._pre is None
                               and classes.intersection({"math", "math-tex"}) else None)

    def handle_data(self, data):
        if self._ignored:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._depth:
            self._append(data if self._pre is not None else re.sub(r"\s+", " ", data))

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._ignored:
            self._ignored -= 1
            return
        if self._ignored:
            return
        if tag == "title":
            self._in_title = False
        if not self._depth:
            return
        if tag == "span" and self._spans:
            start = self._spans.pop()
            if start is not None:
                formula = "".join(self._parts[start:]).strip()
                if formula and not formula.startswith("$$$"):
                    self._parts[start:] = ["$$$" + formula + "$$$"]
        if tag == "pre" and self._pre is not None:
            marker = f"\x00PRE{len(self._pre_blocks)}\x00"
            self._pre_blocks.append("".join(self._pre))
            self._pre = None
            self._parts.append(marker + "\n\n")
        elif tag in self._BLOCKS:
            self._append("\n\n")
        if tag == "div":
            self._depth -= 1
            if not self._depth:
                self.complete = True

    def result(self) -> tuple[str, str]:
        if not self.complete:
            raise ValueError("Editorial main ttypography body is missing or unclosed")
        text = "".join(self._parts).replace("\xa0", " ")
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        for index, block in enumerate(self._pre_blocks):
            text = text.replace(f"\x00PRE{index}\x00", "```\n" + block + "\n```")
        if not text.strip():
            raise ValueError("Editorial main body is empty")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"Editorial text exceeds {MAX_TEXT_CHARS} characters")
        return text, " ".join("".join(self.title_parts).split())


def _get_html(session, url: str, timeout: float) -> str:
    # Redirects are deliberately disabled: even a trusted page must never turn
    # retrieval into an arbitrary external request through a redirect.
    response = session.get(url, timeout=timeout, stream=True, allow_redirects=False)
    try:
        if response.status_code != 200:
            raise RuntimeError(f"Editorial retrieval failed: HTTP {response.status_code} ({url})")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > MAX_HTTP_BYTES:
            raise ValueError(f"HTTP content exceeds {MAX_HTTP_BYTES} bytes ({url})")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content():
            size += len(chunk)
            if size > MAX_HTTP_BYTES:
                raise ValueError(f"HTTP content exceeds {MAX_HTTP_BYTES} bytes ({url})")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        response.close()


def _fetch_sync(contest_id: int, timeout: float) -> dict[str, str] | None:
    from curl_cffi import requests

    contest_url = _CONTEST_URL.format(contest_id=contest_id)
    with requests.Session(impersonate="chrome") as session:
        contest = _ContestParser(contest_url)
        contest.feed(_get_html(session, contest_url, timeout))
        contest.close()
        url = contest.editorial_url()
        if url is None:
            return None
        editorial = _EditorialParser()
        editorial.feed(_get_html(session, url, timeout))
        editorial.close()
        text, title = editorial.result()
        return {"url": url, "text": text, "title": title}


async def fetch_official_editorial(contest_id: int, timeout=20) -> dict[str, str] | None:
    """Return the contest-linked editorial, or None when no trusted link exists.

    HTTP, transport, size-limit and parsing errors propagate to the caller. The
    whole editorial is returned without interpretation or per-problem slicing.
    ``timeout`` applies to each HTTP request in the worker thread.
    """
    if isinstance(contest_id, bool) or not isinstance(contest_id, int) or contest_id <= 0:
        raise ValueError("contest_id must be a positive integer")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    return await asyncio.to_thread(_fetch_sync, contest_id, timeout)
