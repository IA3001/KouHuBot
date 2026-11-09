"""把题面渲染成图片。

为什么不用 HTML 截图：无头浏览器要 300MB 以上内存，2C2G 的机器扛不住，
而且每次出图都要冷启动。这里用 Pillow 直接画，常驻内存很低，出图也快。

样式目标是「在 QQ 里一眼能读」：浅色底、大字号、代码块统一底色，
难度用颜色区分。
"""

from __future__ import annotations

import html as html_lib
import os
import re
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

# 色板
_BG = "#FFFFFF"
_TITLE_FG = "#111827"
_BODY_FG = "#1F2937"
_MUTED_FG = "#6B7280"
_RULE = "#E5E7EB"
_CODE_BG = "#F3F4F6"
_CODE_FG = "#111827"
_DIFF_FG = {"EASY": "#059669", "MEDIUM": "#D97706", "HARD": "#DC2626"}
_DIFF_LABEL = {"EASY": "简单", "MEDIUM": "中等", "HARD": "困难"}

# 排版参数
_PADDING = 40
_TITLE_SIZE = 32
_META_SIZE = 18
_BODY_SIZE = 21
_CODE_SIZE = 17
_FOOT_SIZE = 16
_LINE_RATIO = 1.55
_MAX_HEIGHT = 3200

_FONT_DIRS = [
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/truetype/wqy",
    "/usr/share/fonts/truetype/arphic",
    "/System/Library/Fonts",
    "/Library/Fonts",
    "C:/Windows/Fonts",
]

_REGULAR_FILES = [
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKsc-Regular.otf",
    "NotoSansSC-Regular.otf",
    "wqy-zenhei.ttc",
    "wqy-microhei.ttc",
    "msyh.ttc",
    "PingFang.ttc",
    "simhei.ttf",
]
_BOLD_FILES = [
    "NotoSansCJK-Bold.ttc",
    "NotoSansCJKsc-Bold.otf",
    "NotoSansSC-Bold.otf",
    "msyhbd.ttc",
    "simhei.ttf",
]
_MONO_FILES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
]

_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}
_face_cache: dict[str, tuple[str, int] | None] = {}

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_LI_RE = re.compile(r"<li[^>]*>", re.I)
_PARAGRAPH_END_RE = re.compile(r"</(?:p|div|blockquote|h[1-6])\s*>", re.I)
_BLOCK_END_RE = re.compile(r"</(?:ul|ol|tr|table)\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

# 中文排版禁则：这些标点不能出现在行首，换行时要拉回上一行
_NO_LINE_START = set("，。、；：？！）〕］｝》」』”’,.;:?!)]}%…·")
_NO_LINE_END = set("（〔［｛《「『“‘([{")


# ----------------------------------------------------------------------
# HTML -> 文本块
# ----------------------------------------------------------------------
def _text_from_fragment(fragment: str) -> str:
    # 先压掉源码里的缩进和换行，否则 <ul>\n<li> 之间会多出空行
    text = re.sub(r"\s+", " ", fragment)
    text = _BR_RE.sub("\n", text)
    text = _LI_RE.sub("\n• ", text)
    text = _PARAGRAPH_END_RE.sub("\n\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    collapsed: list[str] = []
    for line in lines:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()


def _code_from_fragment(fragment: str) -> str:
    text = _TAG_RE.sub("", fragment)
    return html_lib.unescape(text).replace("\xa0", " ").strip("\n")


def html_to_blocks(content_html: str) -> list[dict]:
    """把题面 HTML 拆成 [{"type": "text"|"code", "text": ...}]。"""
    if not content_html:
        return []

    blocks: list[dict] = []
    cursor = 0
    for match in _PRE_RE.finditer(content_html):
        if match.start() > cursor:
            chunk = _text_from_fragment(content_html[cursor : match.start()])
            if chunk:
                blocks.append({"type": "text", "text": chunk})
        code = _code_from_fragment(match.group(1))
        if code:
            blocks.append({"type": "code", "text": code})
        cursor = match.end()

    if cursor < len(content_html):
        chunk = _text_from_fragment(content_html[cursor:])
        if chunk:
            blocks.append({"type": "text", "text": chunk})

    if not blocks:
        chunk = _text_from_fragment(content_html)
        if chunk:
            blocks.append({"type": "text", "text": chunk})
    return blocks


def blocks_to_plain(blocks: list[dict]) -> str:
    """纯文本版本，给不渲染的场合（日志、兜底消息）用。"""
    parts = []
    for block in blocks:
        parts.append(block["text"])
    return "\n\n".join(parts).strip()


# ----------------------------------------------------------------------
# 字体
# ----------------------------------------------------------------------
def _locate(filename: str) -> str | None:
    if os.path.isabs(filename):
        return filename if os.path.isfile(filename) else None
    for directory in _FONT_DIRS:
        candidate = os.path.join(directory, filename)
        if os.path.isfile(candidate):
            return candidate
    return None


def _cjk_face_index(path: str) -> int:
    """.ttc 里挑「简体中文」那一款；挑不到就用第 0 款。"""
    for index in range(12):
        try:
            probe = ImageFont.truetype(path, 20, index=index)
            name = " ".join(str(part) for part in probe.getname())
        except Exception:  # noqa: BLE001 - 超出字体face数会抛异常
            break
        if "SC" in name or "Simplified" in name or "简体" in name:
            return index
    return 0


def _resolve(filenames: list[str]) -> tuple[str, int] | None:
    key = "|".join(filenames)
    if key in _face_cache:
        return _face_cache[key]
    resolved = None
    for filename in filenames:
        path = _locate(filename)
        if path:
            index = _cjk_face_index(path) if path.lower().endswith(".ttc") else 0
            resolved = (path, index)
            break
    _face_cache[key] = resolved
    return resolved


def _font(size: int, kind: str = "regular") -> ImageFont.FreeTypeFont:
    cache_key = (size, kind)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    if kind == "mono":
        for filename in _MONO_FILES:
            path = _locate(filename)
            if path:
                font = ImageFont.truetype(path, size)
                _font_cache[cache_key] = font
                return font

    pool = _BOLD_FILES if kind == "bold" else _REGULAR_FILES
    resolved = _resolve(pool) or _resolve(_REGULAR_FILES)
    if resolved is None:
        font = ImageFont.load_default()
    else:
        path, index = resolved
        font = ImageFont.truetype(path, size, index=index)
    _font_cache[cache_key] = font
    return font


def available_font() -> str | None:
    """当前环境能找到的中文字体路径，找不到返回 None。"""
    resolved = _resolve(_REGULAR_FILES)
    return resolved[0] if resolved else None


# ----------------------------------------------------------------------
# 排版
# ----------------------------------------------------------------------
def _tokenize(line: str) -> list[str]:
    """中文按字切，英文/数字按词切，保证换行不把单词劈开。"""
    tokens: list[str] = []
    buffer = ""
    for char in line:
        if char.isascii() and (char.isalnum() or char in "_+-*/=<>!&|^%.,;:'\"()[]{}#@$~`\\"):
            buffer += char
            continue
        if buffer:
            tokens.append(buffer)
            buffer = ""
        tokens.append(char)
    if buffer:
        tokens.append(buffer)
    return tokens


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.split("\n"):
        if not raw_line.strip():
            lines.append("")
            continue
        current = ""
        for token in _tokenize(raw_line):
            trial = current + token
            if draw.textlength(trial, font=font) <= max_width or not current:
                current = trial
            else:
                lines.append(current.rstrip())
                current = "" if token.isspace() else token
        if current.strip():
            lines.append(current.rstrip())
    return _apply_kinsoku(lines)


def _apply_kinsoku(lines: list[str]) -> list[str]:
    """简单的中文禁则处理：
    - 行首不放收尾类标点，把它拉到上一行末尾
    - 行尾不放开头类标点，把它推到下一行开头
    """
    if len(lines) <= 1:
        return lines

    fixed: list[str] = []
    for line in lines:
        if not line:
            # 空行代表段落分隔，必须原样保留，否则段落会挤成一坨
            if fixed and fixed[-1] != "":
                fixed.append("")
            continue
        while fixed and fixed[-1] != "" and line[0] in _NO_LINE_START:
            fixed[-1] += line[0]
            line = line[1:]
        if line:
            fixed.append(line)

    for index in range(len(fixed) - 1):
        while fixed[index] and fixed[index][-1] in _NO_LINE_END:
            char = fixed[index][-1]
            fixed[index] = fixed[index][:-1]
            fixed[index + 1] = char + fixed[index + 1]
    return fixed


def _code_line_font(line: str):
    """代码块里含中文就退回中文字体，否则用等宽字体。

    DejaVu Sans Mono 没有中文字形，直接画会出豆腐块；
    系统里也没有 Noto Sans Mono CJK 可用，所以按行切换。
    """
    if line and not line.isascii():
        return _font(_CODE_SIZE)
    return _font(_CODE_SIZE, "mono")


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    clipped = text
    while clipped and draw.textlength(clipped + ellipsis, font=font) > max_width:
        clipped = clipped[:-1]
    return clipped + ellipsis


def render_problem(
    problem: dict,
    out_dir: str | None = None,
    width: int = 900,
) -> str:
    """把一道题渲染成 PNG，返回文件路径。"""
    blocks = html_to_blocks(problem.get("content_html") or "")
    if not blocks:
        blocks = [{"type": "text", "text": "（这道题没有题面内容）"}]

    title_font = _font(_TITLE_SIZE, "bold")
    meta_font = _font(_META_SIZE)
    body_font = _font(_BODY_SIZE)
    code_font = _font(_CODE_SIZE, "mono")
    foot_font = _font(_FOOT_SIZE)

    inner_width = width - _PADDING * 2
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    body_line = int(_BODY_SIZE * _LINE_RATIO)
    code_line = int(_CODE_SIZE * _LINE_RATIO)

    # ---- 第一遍：算所有元素的位置和总高度 ----
    ops: list[dict] = []
    y = _PADDING

    title_lines = _wrap(probe, problem.get("title") or "未命名题目", title_font, inner_width)
    for line in title_lines:
        ops.append({"kind": "text", "text": line, "font": title_font, "x": _PADDING, "y": y, "fill": _TITLE_FG})
        y += int(_TITLE_SIZE * _LINE_RATIO)

    difficulty = (problem.get("difficulty") or "").upper()

    meta_parts = []
    if problem.get("frontend_id"):
        meta_parts.append(f"LeetCode {problem['frontend_id']}")
    if difficulty:
        meta_parts.append(_DIFF_LABEL.get(difficulty, difficulty))
    if problem.get("tags"):
        meta_parts.append(" / ".join(problem["tags"][:4]))
    meta_text = "·".join(meta_parts) if meta_parts else ""
    if meta_text:
        ops.append(
            {
                "kind": "text",
                "text": _truncate_to_width(probe, meta_text, meta_font, inner_width),
                "font": meta_font,
                "x": _PADDING,
                "y": y,
                "fill": _DIFF_FG.get(difficulty, _MUTED_FG),
            }
        )
        y += int(_META_SIZE * _LINE_RATIO) + 6

    ops.append({"kind": "rule", "y": y, "x": _PADDING, "w": inner_width})
    y += 24

    truncated = False
    for block in blocks:
        if y > _MAX_HEIGHT:
            truncated = True
            break
        if block["type"] == "code":
            lines = block["text"].split("\n")
            drawn = []
            for line in lines:
                font_for_line = _code_line_font(line)
                drawn.append(
                    (
                        _truncate_to_width(probe, line if line else " ", font_for_line, inner_width - 24),
                        font_for_line,
                    )
                )
            box_h = len(drawn) * code_line + 20
            if y + box_h > _MAX_HEIGHT:
                truncated = True
                break
            ops.append({"kind": "code_box", "x": _PADDING, "y": y, "w": inner_width, "h": box_h})
            inner_y = y + 10
            for line_text, line_font in drawn:
                ops.append({"kind": "text", "text": line_text, "font": line_font, "x": _PADDING + 12, "y": inner_y, "fill": _CODE_FG})
                inner_y += code_line
            y += box_h + 16
        else:
            for line in _wrap(probe, block["text"], body_font, inner_width):
                if y + body_line > _MAX_HEIGHT:
                    truncated = True
                    break
                if line:
                    ops.append({"kind": "text", "text": line, "font": body_font, "x": _PADDING, "y": y, "fill": _BODY_FG})
                y += body_line
            if truncated:
                break
            y += 10

    if truncated:
        ops.append(
            {
                "kind": "text",
                "text": "……题面较长，完整内容见原题链接",
                "font": meta_font,
                "x": _PADDING,
                "y": y,
                "fill": _MUTED_FG,
            }
        )
        y += int(_META_SIZE * _LINE_RATIO) + 4

    y += 12
    ops.append({"kind": "rule", "y": y, "x": _PADDING, "w": inner_width})
    y += 18

    link = problem.get("link") or ""
    if link:
        ops.append(
            {
                "kind": "text",
                "text": _truncate_to_width(probe, link, foot_font, inner_width),
                "font": foot_font,
                "x": _PADDING,
                "y": y,
                "fill": _MUTED_FG,
            }
        )
        y += int(_FOOT_SIZE * _LINE_RATIO)

    total_height = y + _PADDING

    # ---- 第二遍：真正绘制 ----
    image = Image.new("RGB", (width, total_height), _BG)
    draw = ImageDraw.Draw(image)
    for op in ops:
        if op["kind"] == "text":
            draw.text((op["x"], op["y"]), op["text"], font=op["font"], fill=op["fill"])
        elif op["kind"] == "rule":
            draw.line([(op["x"], op["y"]), (op["x"] + op["w"], op["y"])], fill=_RULE, width=1)
        elif op["kind"] == "code_box":
            draw.rounded_rectangle(
                [(op["x"], op["y"]), (op["x"] + op["w"], op["y"] + op["h"])],
                radius=8,
                fill=_CODE_BG,
            )

    directory = out_dir or tempfile.gettempdir()
    os.makedirs(directory, exist_ok=True)
    filename = f"kouhu_{problem.get('slug', 'problem')}_{int(time.time() * 1000)}.png"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
    output_path = os.path.join(directory, safe_name)
    image.save(output_path, "PNG", optimize=True)
    return output_path
