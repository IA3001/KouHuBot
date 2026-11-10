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

# 公式归一化在 sources/codeforces.py 里（和题面解析放在一起）。
# 这里必须兼容两种加载方式，否则线上公式会静默退化成源码：
#   - AstrBot 里：插件是包，走相对导入
#   - 本地测试：main.py 是顶层模块，走绝对导入
try:
    from .sources.codeforces import normalize_tex as _normalize_tex
except ImportError:  # pragma: no cover - 仅本地测试走这条
    from sources.codeforces import normalize_tex as _normalize_tex

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
        # line 可能被剥空（整行都是禁则标点），必须先判空再取 line[0]
        while line and fixed and fixed[-1] != "" and line[0] in _NO_LINE_START:
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


def build_meta_text(problem: dict, show_meta: bool = False) -> str:
    """题面顶部那行小字：LeetCode 编号 · 难度 · 标签。

    难度和标签默认**不显示**——「Array」「双指针」这种标签等于直接告诉
    做题的人该用什么做法，题还没做就先泄底了。只有答对（或放弃）之后
    才通过 show_meta=True 展示。
    """
    parts = []
    if problem.get("frontend_id"):
        parts.append(f"LeetCode {problem['frontend_id']}")
    if show_meta:
        difficulty = (problem.get("difficulty") or "").upper()
        if difficulty:
            parts.append(_DIFF_LABEL.get(difficulty, difficulty))
        tags = problem.get("tags") or []
        if tags:
            parts.append(" / ".join(tags[:4]))
    return "·".join(parts)


def render_problem(
    problem: dict,
    out_dir: str | None = None,
    width: int = 900,
    show_meta: bool = False,
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

    # 标题为空就整段不画。坑点题靠这个把答案藏起来——
    # 坑点的 title 本身就是答案（比如「cin 没关同步导致 TLE」），
    # 发到群里等于直接把谜底写在脸上。
    title = problem.get("title") or ""
    if title:
        for line in _wrap(probe, title, title_font, inner_width):
            ops.append({"kind": "text", "text": line, "font": title_font, "x": _PADDING, "y": y, "fill": _TITLE_FG})
            y += int(_TITLE_SIZE * _LINE_RATIO)

    difficulty = (problem.get("difficulty") or "").upper()

    meta_text = build_meta_text(problem, show_meta=show_meta)
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

    # 没有标题也没有 meta 时（坑点题就是这样），顶部不画分隔线也不留空，
    # 否则卡片顶上会挂一条莫名其妙的横线加一段空白。
    if y > _PADDING:
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
    # slug 里可能有冒号等字符（坑点题是 grm:<id>），统一清洗成安全文件名
    safe_slug = re.sub(r"[^0-9A-Za-z_-]+", "-", str(problem.get("slug", "problem")))
    filename = f"kouhu_{safe_slug}_{int(time.time() * 1000)}.png"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
    output_path = os.path.join(directory, safe_name)
    image.save(output_path, "PNG", optimize=True)
    return output_path


# --------------------------------------------------------------------------
# 排行榜
# --------------------------------------------------------------------------

_RANK_GOLD = "#B8860B"
_RANK_SILVER = "#6B7280"
_RANK_BRONZE = "#A0522D"
_RANK_STRIPE = "#F9FAFB"
_RANK_ROW_H = 54

# 中文像素字体里没有 emoji 字形，直接画就是一串豆腐块（□）。
# 排行榜是给人看的，宁可把 emoji 去掉也不要满屏方框。
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # 表情、符号、交通、旗帜等
    "\U00002600-\U000027BF"  # 杂项符号 / 装饰符号
    "\U00002190-\U000021FF"  # 箭头
    "\U00002B00-\U00002BFF"  # 杂项符号和箭头
    "\U0000FE00-\U0000FE0F"  # 变体选择符
    "\U0000200D"             # 零宽连接符
    "\U000020E3"             # 组合用包围键帽
    "\U00003030\U0000303D\U00003297\U00003299"
    "]+"
)


def _display_name(name: str) -> str:
    """去掉字体画不出来的 emoji，避免整行变成豆腐块。"""
    cleaned = _EMOJI_RE.sub("", str(name)).strip()
    return cleaned or "匿名"



def render_leaderboard(
    sections: list[dict],
    out_dir: str | None = None,
    *,
    title: str = "本群排行榜",
    footer: str | None = None,
    width: int = 900,
) -> str:
    """把排行榜画成 PNG。

    sections 是若干段榜单：[{"title": "算法题榜", "rows": [...]}, ...]。
    一张图里放两段，比发两条消息清爽，也不会刷屏。

    用图片而不是文字，是因为群消息有长度上限，条目一多就被截断；
    图片只影响高度，榜单开多长都行。
    """
    title_font = _font(_TITLE_SIZE, "bold")
    section_font = _font(24, "bold")
    head_font = _font(_META_SIZE)
    row_font = _font(_BODY_SIZE)
    foot_font = _font(_FOOT_SIZE)

    inner_width = width - _PADDING * 2
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    # 列宽：排名 / 昵称 / 总分 / AC
    rank_w = 76
    score_w = 150
    ac_w = 150
    name_w = inner_width - rank_w - score_w - ac_w
    name_x = _PADDING + rank_w
    score_right = _PADDING + inner_width - ac_w - 16
    ac_right = _PADDING + inner_width

    ops: list[dict] = []
    y = _PADDING

    ops.append(
        {"kind": "text", "text": title, "font": title_font, "x": _PADDING, "y": y, "fill": _TITLE_FG}
    )
    y += int(_TITLE_SIZE * _LINE_RATIO) + 10

    rank_colors = {1: _RANK_GOLD, 2: _RANK_SILVER, 3: _RANK_BRONZE}

    for section_index, section in enumerate(sections):
        if section_index:
            y += 18
            ops.append({"kind": "rule", "y": y, "x": _PADDING, "w": inner_width})
            y += 22

        ops.append(
            {
                "kind": "text",
                "text": section.get("title") or "排行榜",
                "font": section_font,
                "x": _PADDING,
                "y": y,
                "fill": _TITLE_FG,
            }
        )
        y += int(24 * _LINE_RATIO) + 4

        rows = section.get("rows") or []
        if not rows:
            ops.append(
                {
                    "kind": "text",
                    "text": "暂无记录",
                    "font": row_font,
                    "x": _PADDING,
                    "y": y,
                    "fill": _MUTED_FG,
                }
            )
            y += _RANK_ROW_H
            continue

        # 表头
        for text, x, anchor in (
            ("排名", _PADDING + 8, "la"),
            ("昵称", name_x, "la"),
            ("总分", score_right, "ra"),
            ("AC", ac_right, "ra"),
        ):
            ops.append(
                {
                    "kind": "text",
                    "text": text,
                    "font": head_font,
                    "x": x,
                    "y": y,
                    "fill": _MUTED_FG,
                    "anchor": anchor,
                }
            )
        y += int(_META_SIZE * _LINE_RATIO) + 8
        ops.append({"kind": "rule", "y": y, "x": _PADDING, "w": inner_width})
        y += 10

        for index, row in enumerate(rows, start=1):
            if index % 2 == 0:
                ops.append(
                    {
                        "kind": "stripe",
                        "x": _PADDING,
                        "y": y - 6,
                        "w": inner_width,
                        "h": _RANK_ROW_H - 4,
                    }
                )
            ops.append(
                {
                    "kind": "text",
                    "text": f"{index}",
                    "font": row_font,
                    "x": _PADDING + 8,
                    "y": y,
                    "fill": rank_colors.get(index, _BODY_FG),
                }
            )
            name = _display_name(row.get("user_name") or row.get("user_id") or "匿名")
            ops.append(
                {
                    "kind": "text",
                    "text": _truncate_to_width(probe, name, row_font, name_w - 12),
                    "font": row_font,
                    "x": name_x,
                    "y": y,
                    "fill": _BODY_FG,
                }
            )
            ops.append(
                {
                    "kind": "text",
                    "text": f"{int(row.get('total') or 0)}",
                    "font": row_font,
                    "x": score_right,
                    "y": y,
                    "fill": _BODY_FG,
                    "anchor": "ra",
                }
            )
            ops.append(
                {
                    "kind": "text",
                    "text": f"{int(row.get('solved') or 0)}/{int(row.get('tried') or 0)}",
                    "font": row_font,
                    "x": ac_right,
                    "y": y,
                    "fill": _MUTED_FG,
                    "anchor": "ra",
                }
            )
            y += _RANK_ROW_H

    y += 10
    ops.append({"kind": "rule", "y": y, "x": _PADDING, "w": inner_width})
    y += 16

    if footer:
        ops.append(
            {
                "kind": "text",
                "text": _truncate_to_width(probe, footer, foot_font, inner_width),
                "font": foot_font,
                "x": _PADDING,
                "y": y,
                "fill": _MUTED_FG,
            }
        )
        y += int(_FOOT_SIZE * _LINE_RATIO)

    total_height = y + _PADDING

    image = Image.new("RGB", (width, total_height), _BG)
    draw = ImageDraw.Draw(image)
    for op in ops:
        if op["kind"] == "text":
            draw.text(
                (op["x"], op["y"]),
                op["text"],
                font=op["font"],
                fill=op["fill"],
                anchor=op.get("anchor", "la"),
            )
        elif op["kind"] == "rule":
            draw.line([(op["x"], op["y"]), (op["x"] + op["w"], op["y"])], fill=_RULE, width=1)
        elif op["kind"] == "stripe":
            draw.rectangle(
                [(op["x"], op["y"]), (op["x"] + op["w"], op["y"] + op["h"])],
                fill=_RANK_STRIPE,
            )

    directory = out_dir or tempfile.gettempdir()
    os.makedirs(directory, exist_ok=True)
    filename = f"kouhu_rank_{int(time.time() * 1000)}.png"
    output_path = os.path.join(directory, filename)
    image.save(output_path, "PNG", optimize=True)
    return output_path


# --------------------------------------------------------------------------
# Codeforces 题面渲染
# --------------------------------------------------------------------------
# CF 题面里混着 $$$...$$$ 包起来的 LaTeX，单纯画文字只能看到源码。
# 这里把公式用 matplotlib mathtext 画成小图，再按「文字 + 图」混排进同一行。
#
# 为什么用 mathtext 而不是完整 LaTeX：后者要装 TeX 发行版（上百 MB），
# 2C2G 的机器放不下。mathtext 是 matplotlib 自带的子集，实测覆盖 CF 题面
# 里的公式可达 100%（CF 用了 \le \ge \lt \gt 等非标准别名，需要先归一化）。

_TEX_DPI = 200
_TEX_SIZE_PT = 10          # mathtext 基准字号，配合 200dpi 正好和 21px 正文相当
_tex_cache: dict[str, Image.Image | None] = {}
_mathtext_ready = False


def _ensure_mathtext() -> bool:
    """延迟导入 matplotlib，并把它的一次性缓存目录指到可写位置。"""
    global _mathtext_ready
    if _mathtext_ready:
        return True
    try:
        import matplotlib

        matplotlib.use("Agg")
        # 容器里 $HOME 可能只读，matplotlib 会因此刷一堆警告并反复重建缓存
        os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "mpl-kouhu"))
        _mathtext_ready = True
        return True
    except Exception:                                  # pragma: no cover
        return False


def tex_image(tex: str) -> Image.Image | None:
    """把一段 LaTeX 渲染成 PIL 图像。失败返回 None，调用方退回纯文本。"""
    tex = (tex or "").strip()
    if not tex:
        return None
    if tex in _tex_cache:
        cached = _tex_cache[tex]
        return cached.copy() if cached is not None else None
    if not _ensure_mathtext():
        _tex_cache[tex] = None
        return None

    import io
    from matplotlib import mathtext

    normalize_tex = _normalize_tex

    body = normalize_tex(tex)
    image: Image.Image | None = None
    try:
        buf = io.BytesIO()
        mathtext.math_to_image(f"${body}$", buf, dpi=_TEX_DPI, format="png")
        buf.seek(0)
        image = Image.open(buf).convert("RGBA")
    except Exception:
        image = None
    if image is None:
        # 归一化救不回来的，用原始写法再试一次
        try:
            buf = io.BytesIO()
            mathtext.math_to_image(f"${tex}$", buf, dpi=_TEX_DPI, format="png")
            buf.seek(0)
            image = Image.open(buf).convert("RGBA")
        except Exception:
            image = None
    _tex_cache[tex] = image
    return image.copy() if image is not None else None


_RICH_RE = re.compile(r"\$\$\$(.+?)\$\$\$", re.S)


def rich_tokens(text: str) -> list[tuple[str, str]]:
    """把含 $$$...$$$ 的文本切成 [('text', ...), ('tex', ...)] 序列。"""
    tokens: list[tuple[str, str]] = []
    pos = 0
    for m in _RICH_RE.finditer(text):
        if m.start() > pos:
            tokens.append(("text", text[pos:m.start()]))
        tokens.append(("tex", m.group(1)))
        pos = m.end()
    if pos < len(text):
        tokens.append(("text", text[pos:]))
    return tokens


def _split_words(chunk: str) -> list[str]:
    """把文字切成可换行的最小单元：中文按字，英文按词。"""
    out: list[str] = []
    buf = ""
    for ch in chunk:
        if ch.isascii() and (ch.isalnum() or ch in "_-'\".,;:!?()[]{}<>=+*/%^&|~`"):
            buf += ch
        elif ch == " ":
            if buf:
                out.append(buf)
                buf = ""
            out.append(" ")
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
    if buf:
        out.append(buf)
    return out


def _layout_rich(
    paragraphs: list[str],
    font: ImageFont.FreeTypeFont,
    inner_width: int,
    line_height: int,
) -> list[dict]:
    """把若干段落排成行。每行是 [{'kind':'text'|'img', ...}] 的列表。"""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines: list[dict] = []

    first = True
    for para in paragraphs:
        if not para.strip():
            continue
        if not first:
            # 段落之间留一点空隙，否则整段题面糊成一块
            lines.append({"items": [], "height": int(line_height * 0.55)})
        first = False
        # 段内的单个换行（比如 <li> 拆出来的项）是强制断行，不能当普通空白
        for sub in para.split("\n"):
            current: list[dict] = []
            width = 0
            for kind, value in rich_tokens(sub):
                if kind == "tex":
                    img = tex_image(value)
                    if img is not None:
                        if img.width > inner_width:        # 超宽的公式缩到行宽
                            ratio = inner_width / img.width
                            img = img.resize((inner_width, max(1, int(img.height * ratio))))
                        if current and width + img.width > inner_width:
                            lines.append({"items": current, "height": line_height})
                            current, width = [], 0
                        current.append({
                            "kind": "img", "image": img,
                            "width": img.width, "height": img.height,
                        })
                        width += img.width
                        continue
                    # 渲染不出来就把公式源码当文字放，至少信息不丢
                    value = value.strip()

                for word in _split_words(value):
                    w = probe.textlength(word, font=font)
                    if current and width + w > inner_width:
                        lines.append({"items": current, "height": line_height})
                        current, width = [], 0
                    if word == " " and not current:
                        continue
                    current.append({"kind": "text", "text": word, "width": w})
                    width += w
            lines.append({"items": current, "height": line_height})
    return lines


def _line_pixel_height(line: dict, line_height: int) -> int:
    """一行里如果嵌了比正文高的公式，行高要跟着撑开。"""
    tallest = line_height
    for item in line["items"]:
        if item["kind"] == "img":
            tallest = max(tallest, item["height"] + 6)
    return tallest


def _render_cf_card(
    *,
    header_lines: list[str],
    paragraphs: list[str],
    examples: list[tuple[str, str]] | None,
    footer: str,
    out_dir: str | None,
    width: int,
    name_hint: str,
) -> str:
    """画一张 CF 题面卡。paragraphs 里可以带 $$$...$$$ 公式。"""
    title_font = _font(_TITLE_SIZE, "bold")
    meta_font = _font(_META_SIZE)
    body_font = _font(_BODY_SIZE)
    code_font = _font(_CODE_SIZE, "mono")
    foot_font = _font(_FOOT_SIZE)

    inner_width = width - _PADDING * 2
    body_line = int(_BODY_SIZE * _LINE_RATIO)
    code_line = int(_CODE_SIZE * _LINE_RATIO)

    ops: list[dict] = []
    y = _PADDING

    for line in header_lines:
        for wrapped in _wrap(
            ImageDraw.Draw(Image.new("RGB", (8, 8))), line, title_font if header_lines.index(line) == 0 else meta_font, inner_width
        ):
            font = title_font if header_lines.index(line) == 0 else meta_font
            ops.append({
                "kind": "text", "text": wrapped, "font": font, "x": _PADDING, "y": y,
                "fill": _TITLE_FG if font is title_font else _MUTED_FG,
            })
            y += int(font.size * _LINE_RATIO)
        y += 4

    y += 8
    ops.append({"kind": "rule", "y": y, "x": _PADDING, "w": inner_width})
    y += 24

    for line in _layout_rich(paragraphs, body_font, inner_width, body_line):
        h = _line_pixel_height(line, body_line)
        if y + h > _MAX_HEIGHT:
            ops.append({
                "kind": "text", "text": "……题面较长，完整内容见原题",
                "font": meta_font, "x": _PADDING, "y": y, "fill": _MUTED_FG,
            })
            y += int(_META_SIZE * _LINE_RATIO)
            break
        if not line["items"]:
            y += h
            continue
        x = _PADDING
        baseline_top = y + max(0, (h - body_line) // 2)
        for item in line["items"]:
            if item["kind"] == "text":
                ops.append({
                    "kind": "text", "text": item["text"], "font": body_font,
                    "x": int(x), "y": baseline_top, "fill": _BODY_FG,
                })
            else:
                top = y + max(0, (h - item["height"]) // 2)
                ops.append({
                    "kind": "image", "image": item["image"],
                    "x": int(x), "y": int(top),
                })
            x += item["width"]
        y += h

    for label, block in (examples or []):
        y += 14
        ops.append({"kind": "text", "text": label, "font": meta_font, "x": _PADDING, "y": y, "fill": _MUTED_FG})
        y += int(_META_SIZE * _LINE_RATIO)
        rows = block.split("\n") or [""]
        box_h = max(1, len(rows)) * code_line + 20
        if y + box_h > _MAX_HEIGHT:
            break
        ops.append({"kind": "code_box", "x": _PADDING, "y": y, "w": inner_width, "h": box_h})
        inner_y = y + 10
        for row in rows:
            ops.append({
                "kind": "text",
                "text": _truncate_to_width(ImageDraw.Draw(Image.new("RGB", (8, 8))), row or " ", code_font, inner_width - 24),
                "font": code_font, "x": _PADDING + 12, "y": inner_y, "fill": _CODE_FG,
            })
            inner_y += code_line
        y += box_h + 10

    y += 16
    ops.append({"kind": "rule", "y": y, "x": _PADDING, "w": inner_width})
    y += 18
    if footer:
        for wrapped in _wrap(ImageDraw.Draw(Image.new("RGB", (8, 8))), footer, foot_font, inner_width):
            ops.append({"kind": "text", "text": wrapped, "font": foot_font, "x": _PADDING, "y": y, "fill": _MUTED_FG})
            y += int(_FOOT_SIZE * _LINE_RATIO)

    total_height = max(y + _PADDING, _PADDING * 2 + 40)
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
                radius=8, fill=_CODE_BG,
            )
        elif op["kind"] == "image":
            img = op["image"]
            image.paste(img, (op["x"], op["y"]), img if img.mode == "RGBA" else None)

    directory = out_dir or tempfile.gettempdir()
    os.makedirs(directory, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_-]+", "-", str(name_hint))
    path = os.path.join(directory, f"kouhu_{safe}_{int(time.time() * 1000)}.png")
    image.save(path, "PNG", optimize=True)
    return path


def _opaque_name(suffix: str) -> str:
    """给临时图片起个不含题号的名字。

    以前用的是 cf_key（如 1542E2-q.png），万一哪天被平台当成文件名展示出来，
    题号就泄露了。这里换成随机串，反正文件用完就删。
    """
    import uuid

    return f"{uuid.uuid4().hex[:10]}-{suffix}"


def render_cf_statement(problem: dict, out_dir: str | None = None, width: int = 900) -> str:
    """题面正文图。

    刻意**不显示题目名字和题号**：有题号就能直接搜题解，题目就白出了。
    也**不显示难度和分值**——看到「CF 2600 / 1950 分」会直接劝退或者反向
    暴露这题大概要用什么级别的算法。名字和题号在判 AC / 放弃时才揭晓。
    """
    try:
        from .browser_render import render_statement
    except ImportError:
        from browser_render import render_statement
    return render_statement(problem, out_dir, width)


def render_cf_examples(problem: dict, out_dir: str | None = None, width: int = 900) -> str:
    """样例图。样例是验证理解的关键，单独一张，别挤在正文里。"""
    try:
        from .browser_render import render_examples
    except ImportError:
        from browser_render import render_examples
    return render_examples(problem, out_dir, width)
