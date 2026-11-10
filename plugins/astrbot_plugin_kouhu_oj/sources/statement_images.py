"""Fetch CF statement images into inert, bounded PNG data URIs.

Only this source-side helper uses the network. Renderers consume the cached data.
"""
from __future__ import annotations

import base64
from io import BytesIO
import warnings
from urllib.parse import urljoin, urlsplit

MAX_IMAGES = 12
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_REDIRECTS = 3
TIMEOUT = 12
ALLOWED_HOSTS = frozenset({"codeforces.com", "codeforces.org", "espresso.codeforces.com", "assets.codeforces.com"})


def validate_url(url: str) -> str:
    """Reject non-HTTPS destinations, credentials, ports and ambiguous URLs."""
    if not isinstance(url, str) or not url or any(ord(c) <= 32 for c in url) or "\\" in url:
        raise ValueError("图片地址无效")
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS
            or parts.username is not None or parts.password is not None
            or parts.netloc.lower() != parts.hostname or parts.fragment):
        raise ValueError("图片地址不在受信任的 CF HTTPS 域名中")
    return url


def _png(data: bytes) -> bytes:
    from PIL import Image

    if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")
            or data.startswith((b"GIF87a", b"GIF89a"))
            or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
        raise ValueError("不支持的图片格式（仅 PNG/JPEG/GIF/WebP，SVG 不受支持）")

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise ValueError("不支持的图片格式（仅 PNG/JPEG/GIF/WebP，SVG 不受支持）")
            if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_PIXELS:
                raise ValueError("图片尺寸超过 2000 万像素限制")
            image.load()
            # Rebuild pixels without original metadata, animation or embedded payloads.
            clean = Image.new("RGBA", image.size)
            clean.paste(image.convert("RGBA"))
            output = BytesIO()
            clean.save(output, format="PNG")
            return output.getvalue()


def download_images(urls: list[str]) -> list[dict[str, str]]:
    """Return one positional record per URL, including visible failure details.

    Requests stream under both per-image and batch budgets. Redirects are followed
    manually only after applying the same allowlist to each destination.
    """
    records = []
    session = None
    received = 0
    stored = 0
    try:
        for index, original in enumerate(urls):
            record = {"token": f"KOUHUIMAGE{index}END", "url": original, "src": ""}
            records.append(record)
            try:
                if index >= MAX_IMAGES:
                    raise ValueError("图片数量超过 12 张限制")
                current = validate_url(original)
                if received >= MAX_TOTAL_BYTES or stored >= MAX_TOTAL_BYTES:
                    raise ValueError("图片总大小超过 12MB 限制")
                if session is None:
                    from curl_cffi import requests
                    session = requests.Session(impersonate="chrome")
                for hop in range(MAX_REDIRECTS + 1):
                    response = session.get(current, timeout=TIMEOUT, allow_redirects=False, stream=True)
                    try:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if hop == MAX_REDIRECTS:
                                raise ValueError("图片重定向次数过多")
                            location = response.headers.get("Location") or response.headers.get("location")
                            if not location:
                                raise ValueError("图片重定向地址缺失")
                            current = validate_url(urljoin(current, location))
                            continue
                        if response.status_code != 200:
                            raise ValueError(f"图片下载失败 HTTP {response.status_code}")
                        length = response.headers.get("Content-Length") or response.headers.get("content-length")
                        budget = min(MAX_IMAGE_BYTES, MAX_TOTAL_BYTES - received)
                        if length is not None and int(length) > budget:
                            raise ValueError("图片文件超过大小限制")
                        chunks = []
                        size = 0
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            size += len(chunk)
                            received += len(chunk)
                            if size > MAX_IMAGE_BYTES or received > MAX_TOTAL_BYTES:
                                raise ValueError("图片文件超过大小限制")
                            chunks.append(chunk)
                        data = _png(b"".join(chunks))
                        if len(data) > MAX_IMAGE_BYTES or stored + len(data) > MAX_TOTAL_BYTES:
                            raise ValueError("转换后图片超过大小限制")
                        stored += len(data)
                        record["src"] = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
                        break
                    finally:
                        response.close()
            except Exception as exc:
                # Stable Chinese warnings stay visible even without image rendering.
                record["error"] = f"〔图片加载失败：{exc}〕"
    finally:
        if session is not None:
            session.close()
    return records
