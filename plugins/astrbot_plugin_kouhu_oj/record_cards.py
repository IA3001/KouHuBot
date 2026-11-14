"""记录类输出的图片渲染：错题本、个人历史、战绩、提交详情、题后解析。

设计口径（与题面卡片 ``browser_render`` 共用一套品牌语言）：

- 纯白偏暖纸色（``#fcfcfa``），左侧 3~4px 品牌绿竖线，分区标题用深绿
  ``#3f5f47``，正文 ``#202124``，等宽用 ``DejaVu Sans Mono``。
- 判词用「色带 + 徽章」，不依赖 emoji 字形：容器里没有 emoji 字体，
  ``🟢`` 一类字符会渲染成豆腐块，所以卡片内一律用 CSS 画的色块与文字标签。
- 数据全部由调用方（main.py）预处理好再传进来：本模块**不做任何可见性判断**。
  「进行中隐藏题号」之类的口径必须在调用方先把身份字段替换成文案，否则就
  等于把隐藏逻辑复制两份，迟早走岔。
- 所有外部字符串一律 ``html.escape``；卡片不加载任何远程资源，CSS 内联，
  CSP 只放行内联样式与 ``data:`` 图片，字体走系统字体。
- 用户内容里的 emoji 会被替换成占位点（见 :func:`clean_text`）：这里没有
  emoji 字体，留着就是一串方块，比去掉更难看。

本模块只负责「HTML 字符串 → PNG 路径」。真正的浏览器调用复用
``browser_render.render_html``（单工作线程、每次关浏览器、失败抛
``RenderError``），因此导入本模块不需要 Playwright。
"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable, Sequence

# ------------------------------------------------------------------ 常量

CARD_WIDTH = 900            # 默认宽度；调用方通常传 image_width
CODE_BUDGET = 5000          # 单个代码块的字数预算，超过才分页（见 split_code）
COMMENT_LIMIT = 240         # 列表里一行判词的软上限，超了补省略号
DEFAULT_MAX_HEIGHT = 24_000  # 单张卡片的安全高度，超过由调用方分页

# 一页装多少。图片不受消息长度限制，所以按「一张图尽量装完」来定，
# 不是按「消息发得出去」来定：列表一行约 155px，30 行 ≈ 4.7k px，
# 离 30000px 上限还很远；字始终是同一号字，多的只是高度。
# 文字回退仍按消息长度的老口径（5 / 3 / 5）。
CARD_LIST_PAGE = 30         # history：一页 30 题（约 4.7k px 高）
CARD_ENTRY_PAGE = 20        # history show 题号：一页 20 次作答
CARD_MISTAKE_PAGE = 20      # mistakes：一页 20 题

VERDICT_TONES = {
    "AC": ("ac", "AC · 通过"),
    "WA": ("wa", "WA · 未通过"),
    "PARTIAL": ("partial", "PARTIAL · 部分通过"),
    "GIVEUP": ("giveup", "GIVEUP · 放弃"),
    "GAVE_UP": ("giveup", "GIVEUP · 放弃"),
    "ERROR": ("error", "ERROR · 异常"),
}

# ------------------------------------------------------------------ 文本清洗

# emoji 与变体选择符：容器里没有 emoji 字体，画出来是豆腐块。
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # 麻将/扑克/表情/交通/补充符号
    "\U0001F1E6-\U0001F1FF"   # 区域指示符（国旗）
    "\u2600-\u27BF"           # 杂项符号与装饰符号
    "\u2B00-\u2BFF"           # 杂项符号与箭头
    "\uFE00-\uFE0F"           # 变体选择符
    "\u200D"                  # 零宽连接符
    "\u20E3"                  # 组合包围键帽
    "\u00A9\u00AE\u2122"      # © ® ™
    "]+"
)

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Any, *, placeholder: str = "·") -> str:
    """把外部文本洗成卡片可安全绘制的形态。

    去控制字符、把 emoji 换成 ``placeholder``（连续多个只留一个），
    并折叠掉多余空白。**不做长度截断**——截断由各 builder 自己按语境决定。
    """
    text = _CTRL.sub("", str(value or ""))
    text = _EMOJI.sub(placeholder, text)
    return text


def esc(value: Any) -> str:
    """转义并清洗：所有进入 HTML 的外部字符串都必须经过这里。"""
    return html.escape(clean_text(value), quote=True)


def clip(text: str, limit: int, note: str = "…") -> str:
    """按字符数截断，尽量不切断英文单词。"""
    value = str(text or "").strip()
    if limit <= 0 or len(value) <= limit:
        return value
    head = value[: max(1, limit - len(note))]
    cut = head.rfind(" ")
    if cut >= limit * 3 // 5:
        head = head[:cut]
    return head.rstrip() + note


def code_block(text: str) -> str:
    """等宽块。空内容给一行说明，不留空白框。"""
    body = str(text or "").strip("\n")
    if not body.strip():
        return '<pre class="empty">（这一条没有留下内容）</pre>'
    return f"<pre>{html.escape(clean_text(body), quote=False)}</pre>"


def prose(text: str) -> str:
    """极简 Markdown → HTML：标题、无序/有序列表、``**粗体**``、``\\`代码\\```。

    只用于判词与解析这类模型生成的文本片段。**不做公式解析**：提交与判词里
    的 ``$`` 多半是代码或金额，当数学切会把内容切坏。
    """
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    buffer: list[str] = []
    list_kind = ""

    def flush() -> None:
        if buffer:
            out.append("<p>" + "<br>".join(buffer) + "</p>")
            buffer.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = ""

    for raw in lines:
        line = raw.rstrip()
        heading = re.match(r"^\s*#{1,6}\s+(.+)$", line)
        item = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)$", line)
        if not line.strip():
            flush()
            close_list()
        elif heading:
            flush()
            close_list()
            out.append(f"<h3>{inline(heading[1])}</h3>")
        elif item:
            flush()
            kind = "ol" if re.match(r"^\s*\d", line) else "ul"
            if kind != list_kind:
                close_list()
                out.append(f"<{kind}>")
                list_kind = kind
            out.append(f"<li>{inline(item[1])}</li>")
        elif re.fullmatch(r"\s*[-*_]{3,}\s*", line):
            flush()
            close_list()
            out.append('<div class="hr"></div>')
        else:
            close_list()
            buffer.append(inline(line))
    flush()
    close_list()
    return "\n".join(out) or '<p class="empty">（没有内容）</p>'


def inline(text: str) -> str:
    """行内标记：先转义，再把 ``**粗体**`` 和 ``\\`代码\\``` 换成标签。"""
    value = esc(text)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"`([^`]+?)`", r'<code class="inl">\1</code>', value)
    return value


def split_code(text: str, budget: int = CODE_BUDGET) -> list[str]:
    """把超长内容按预算切成若干块；尽量在换行处切，不切坏单行。

    正常提交（``max_submission_chars`` 默认 2000）不会触发；图片转写最多
    6000 字，最多切成两块。
    """
    body = str(text or "")
    if budget <= 0 or len(body) <= budget:
        return [body]
    chunks: list[str] = []
    remaining = body
    while len(remaining) > budget:
        window = remaining[:budget]
        cut = window.rfind("\n")
        if cut < budget // 2:
            cut = budget
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


# ------------------------------------------------------------------ 样式

CARD_CSS = """
:root {
  --ink: #202124; --muted: #6b7076; --faint: #9aa0a6; --line: #e4e7ea;
  --paper: #fcfcfa; --card: #ffffff; --brand: #62866a; --brand-ink: #3f5f47;
  --ac: #1f7a4d; --ac-bg: #e8f3ec; --wa: #b3392c; --wa-bg: #fbecea;
  --give: #7c7a72; --give-bg: #efefec; --part: #a06a12; --part-bg: #fbf1e0;
  --err: #8a4b1f; --err-bg: #fbeee4;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--paper); color: var(--ink); }
body { font: 17px/1.7 "Arial", "Noto Sans CJK SC", "Noto Sans SC", sans-serif;
       -webkit-font-smoothing: antialiased; }
.sheet { padding: 34px 36px 30px; }
.masthead { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; }
.kicker { font-size: 13px; letter-spacing: .18em; color: var(--brand-ink);
          font-weight: 700; margin: 0 0 6px; text-transform: uppercase; }
h1 { font-size: 30px; line-height: 1.25; margin: 0; font-weight: 700; letter-spacing: -.01em; }
.sub { margin: 10px 0 0; color: var(--muted); font-size: 15px; }
.pager { flex: none; font: 600 14px/1 "DejaVu Sans Mono", monospace; color: var(--brand-ink);
         background: #eef3ef; border: 1px solid #d9e3db; border-radius: 999px;
         padding: 9px 14px; white-space: nowrap; }
.rule { height: 3px;
        background: linear-gradient(90deg, var(--brand) 0 120px, var(--line) 120px 100%);
        margin: 18px 0 22px; }
.stats { display: flex; gap: 10px; margin: 0 0 22px; }
.stat { flex: 1 1 0; background: var(--card); border: 1px solid var(--line);
        border-radius: 10px; padding: 12px 14px; }
.stat .n { font: 700 26px/1.2 "DejaVu Sans Mono", monospace; letter-spacing: -.02em; }
.stat .l { font-size: 13px; color: var(--muted); margin-top: 3px; }
.stat.hero { background: #f2f7f3; border-color: #d7e3da; }
.stat.hero .n { color: var(--brand-ink); }
.rows { display: flex; flex-direction: column; gap: 12px; }
.row { background: var(--card); border: 1px solid var(--line);
       border-left: 4px solid var(--brand); border-radius: 10px; padding: 15px 18px 16px; }
.row.ac { border-left-color: var(--ac); } .row.wa { border-left-color: var(--wa); }
.row.giveup { border-left-color: var(--give); } .row.partial { border-left-color: var(--part); }
.row.error { border-left-color: var(--err); }
.row.hidden { border-left-style: dashed; }
.top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.spacer { flex: 1 1 auto; }
.badge { font: 700 12px/1 "DejaVu Sans Mono", monospace; letter-spacing: .06em;
         padding: 6px 9px; border-radius: 6px; text-transform: uppercase;
         background: #eef3ef; color: var(--brand-ink); white-space: nowrap; }
.badge.ac { background: var(--ac-bg); color: var(--ac); }
.badge.wa { background: var(--wa-bg); color: var(--wa); }
.badge.giveup { background: var(--give-bg); color: var(--give); }
.badge.partial { background: var(--part-bg); color: var(--part); }
.badge.error { background: var(--err-bg); color: var(--err); }
.key { font: 700 20px/1.3 "DejaVu Sans Mono", monospace; letter-spacing: -.01em; }
.key.hidden { font: 600 17px/1.3 inherit; color: var(--muted); }
.tag { font-size: 13px; color: var(--muted); background: #f4f5f6; border-radius: 6px;
       padding: 4px 8px; white-space: nowrap; }
.tag.hot { color: var(--part); background: var(--part-bg); font-weight: 700; }
.meta { margin: 9px 0 0; color: var(--muted); font-size: 14.5px; }
.meta b { color: var(--ink); font-weight: 600; }
.sep { color: #cfd4d8; padding: 0 7px; }
.trail { display: inline-block; margin-left: 10px; font: 600 12.5px/1 "DejaVu Sans Mono", monospace;
         color: var(--muted); background: #f4f5f6; border-radius: 5px; padding: 3px 7px; }
.comment { margin: 10px 0 0; font-size: 16.5px; line-height: 1.65;
           border-left: 2px solid var(--line); padding-left: 12px; color: #33373b; }
.comment.empty { color: var(--faint); }
.actions { margin: 11px 0 0; font-size: 14px; color: var(--muted); }
.cmd { font: 600 14px/1 "DejaVu Sans Mono", monospace; color: var(--brand-ink);
       background: #f2f7f3; border: 1px solid #dbe5dd; border-radius: 5px;
       padding: 4px 7px; white-space: nowrap; }
.section { margin: 24px 0 12px; display: flex; align-items: center; gap: 10px; }
.section h2 { font-size: 15px; margin: 0; color: var(--brand-ink);
              letter-spacing: .04em; white-space: nowrap; }
.section .fill { flex: 1 1 auto; height: 1px; background: var(--line); }
.section .hint { font-size: 12.5px; color: var(--faint); white-space: nowrap; }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; background: #f6f7f9;
      border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px;
      font: 15.5px/1.65 "DejaVu Sans Mono", "Noto Sans CJK SC", monospace; }
pre.empty { color: var(--faint); font-family: inherit; font-size: 15px; }
.body p { margin: 0 0 12px; }
.body p.empty { color: var(--faint); }
.body h3 { color: var(--brand-ink); font-size: 18px; font-weight: 600; margin: 18px 0 8px; }
.body ul, .body ol { margin: 0 0 12px; padding-left: 24px; }
.body li { margin: 4px 0; }
.body .hr { height: 1px; background: var(--line); margin: 16px 0; }
code.inl { font: 14.5px/1.5 "DejaVu Sans Mono", monospace; background: #eef1f3;
           border-radius: 4px; padding: 1px 5px; }
.verdict { display: flex; align-items: center; gap: 14px; background: var(--card);
           border: 1px solid var(--line); border-left: 4px solid var(--brand);
           border-radius: 10px; padding: 14px 18px; margin: 0 0 22px; }
.verdict.wa { border-left-color: var(--wa); } .verdict.ac { border-left-color: var(--ac); }
.verdict.giveup { border-left-color: var(--give); } .verdict.partial { border-left-color: var(--part); }
.verdict .box { display: flex; flex-direction: column; gap: 2px; }
.verdict .big { font: 700 22px/1.2 "DejaVu Sans Mono", monospace; }
.verdict .lbl { font-size: 13px; color: var(--muted); }
.callout { background: var(--part-bg); border: 1px solid #ecd9b6;
           border-left: 4px solid var(--part); border-radius: 10px;
           padding: 14px 16px; font-size: 16.5px; color: #6b4a10; }
.callout .t { font: 700 12px/1 "DejaVu Sans Mono", monospace; letter-spacing: .08em;
              color: var(--part); margin-bottom: 6px; }
/* 反例是「输入 + 两个结果」的多行文本，必须保留换行，否则挤成一行读不出结构。 */
.callout .body { white-space: pre-wrap; overflow-wrap: anywhere; }
.callout.note { background: #f4f6f4; border-color: #dde4de; border-left-color: var(--brand);
                color: #3c4a3f; }
.callout.note .t { color: var(--brand-ink); }
.link { font: 13.5px/1.5 "DejaVu Sans Mono", monospace; color: var(--brand-ink);
        overflow-wrap: anywhere; }
.foot { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 22px;
        padding-top: 16px; border-top: 1px solid var(--line);
        color: var(--muted); font-size: 13.5px; }
.foot.plain { border-top: 0; padding-top: 8px; margin-top: 8px; }
.foot .dot { color: #cfd4d8; }
"""

_HEAD = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
    "style-src 'unsafe-inline'; font-src file: data:; img-src data:\">"
    "<title>{title}</title><style>{css} body {{ width: {width}px; }}</style>"
    "</head><body><div class=\"sheet\">{body}</div></body></html>"
)


def document(*, title: str, body: str, width: int = CARD_WIDTH) -> str:
    """包一层文档外壳。宽度在 CSS 里写死，截图时 viewport 用同一宽度。"""
    _validate_width(width)
    return _HEAD.format(title=html.escape(title), css=CARD_CSS, body=body, width=int(width))


def _validate_width(width: Any) -> int:
    if isinstance(width, bool) or not isinstance(width, int):
        raise ValueError("卡片宽度必须是 320–4096 的整数")
    if not 320 <= width <= 4096:
        raise ValueError("卡片宽度必须是 320–4096 的整数")
    return width


# ------------------------------------------------------------------ 构件

def masthead(*, kicker: str, title: str, subtitle: str = "", pager: str = "") -> str:
    pager_html = f'<div class="pager">{esc(pager)}</div>' if pager else ""
    sub = f'<p class="sub">{esc(subtitle)}</p>' if subtitle else ""
    return (
        '<div class="masthead"><div>'
        f'<p class="kicker">{esc(kicker)}</p><h1>{esc(title)}</h1>{sub}</div>'
        f"{pager_html}</div><div class=\"rule\"></div>"
    )


def section(title: str, hint: str = "") -> str:
    hint_html = f'<span class="hint">{esc(hint)}</span>' if hint else ""
    return (f'<div class="section"><h2>{esc(title)}</h2>'
            f'<div class="fill"></div>{hint_html}</div>')


def footer(pairs: Sequence[tuple[str, str]] = (), notes: Iterable[str] = ()) -> str:
    """``pairs`` 是 (说明, 命令) 列表，``notes`` 是普通提示句。"""
    bits: list[str] = []
    for index, (label, command) in enumerate(pairs):
        if index:
            bits.append('<span class="dot">/</span>')
        bits.append(f"<span>{esc(label)}</span><span class=\"cmd\">{esc(command)}</span>")
    out = f'<div class="foot">{"".join(bits)}</div>' if bits else ""
    for note in notes:
        if str(note).strip():
            out += f'<div class="foot plain">{esc(note)}</div>'
    return out


def stats(cells: Sequence[tuple[str, str]], hero: int = 0) -> str:
    """指标条：``cells`` 是 (数值, 标签) 列表。"""
    parts = []
    for index, (value, label) in enumerate(cells):
        cls = "stat hero" if index == hero else "stat"
        parts.append(f'<div class="{cls}"><div class="n">{esc(value)}</div>'
                     f'<div class="l">{esc(label)}</div></div>')
    return f'<div class="stats">{"".join(parts)}</div>'


def verdict_badge(verdict: str) -> tuple[str, str]:
    """返回 (色调 class, 中文标签)。未知判定落到 neutral 样式。"""
    tone, label = VERDICT_TONES.get(str(verdict or "").upper(), ("", str(verdict or "未知")))
    return tone, label


# ------------------------------------------------------------------ 各卡片

def history_card(
    *,
    rows: Sequence[dict],
    page: int,
    pages: int,
    total: int,
    lane: str = "正题",
    commands: Sequence[tuple[str, str]] = (),
    notes: Iterable[str] = (),
    width: int = CARD_WIDTH,
) -> str:
    """按题汇总的历史列表（``history [页码]``）。

    ``rows`` 每项字段：``key``（已按隐藏口径替换好）、``hidden``、
    ``verdicts``（判词列表）、``count``、``at``（已格式化）、``points``、
    ``hint``、``title``（可选，隐藏时不要传）。
    """
    kicker = f"口胡 OJ · {lane}记录"
    title = f"我的{lane}记录"
    body = masthead(kicker=kicker, title=title,
                    subtitle="按题汇总 · 每题一行 · 跨轮合并",
                    pager=f"{page} / {pages} 页 · 共 {total} 题")
    if not rows:
        body += ('<div class="rows"><div class="row">'
                 '<p class="comment">还没有记录，提交或放弃后会记录在这里。</p>'
                 "</div></div>")
        return document(title=title, body=body + footer(commands, notes), width=width)

    cards = []
    for row in rows:
        hidden = bool(row.get("hidden"))
        # ``verdicts`` 来自 review_problems：**最新的在前**。徽章只画最新一条，
        # 完整的判定轨迹放在 meta 行里（WA → AC），免得一排徽章抢掉题号。
        verdicts = list(row.get("verdicts") or [])
        newest = verdicts[0] if verdicts else ""
        tone, label = verdict_badge(newest)
        badge = (f'<span class="badge {tone}">{esc(label)}</span>' if newest
                 else '<span class="badge">未知判词</span>')
        trail = ""
        if len(verdicts) > 1:
            ordered = " → ".join(verdict_badge(v)[1].split(" · ")[0]
                                 for v in reversed(verdicts))
            trail = f'<span class="trail">判定轨迹 {esc(ordered)}</span>'
        key = str(row.get("key") or "—")
        key_html = (f'<span class="key hidden">{esc(key)}</span>' if hidden
                    else f'<span class="key">{esc(key)}</span>')
        meta = [f"<b>{esc(row.get('at') or '')}</b>",
                f"得分 <b>{esc(row.get('points') or '—')}</b>",
                esc(row.get("hint") or "")]
        title_html = ""
        if row.get("title") and not hidden:
            title_html = f'<p class="meta">{esc(row["title"])}</p>'
        cards.append(
            f'<div class="row {tone}{" hidden" if hidden else ""}">'
            f'<div class="top">{badge}{key_html}'
            f'<span class="spacer"></span>'
            f'<span class="tag">{esc(row.get("count") or "")}</span></div>'
            f'<p class="meta">' + '<span class="sep">·</span>'.join(m for m in meta if m)
            + trail + "</p>" + title_html + "</div>"
        )
    body += f'<div class="rows">{"".join(cards)}</div>'
    return document(title=title, body=body + footer(commands, notes), width=width)


def entries_card(
    *,
    key_label: str,
    rows: Sequence[dict],
    page: int,
    pages: int,
    total: int,
    lane: str = "正题",
    commands: Sequence[tuple[str, str]] = (),
    notes: Iterable[str] = (),
    width: int = CARD_WIDTH,
) -> str:
    """某道题的每次作答（``history show 题号``）。

    ``rows`` 每项：``id``、``verdict``、``at``、``submission``（预览）、
    ``comment``（预览）、``detail_cmd``（看全文的命令）。
    """
    kicker = f"口胡 OJ · {lane}作答"
    title = f"{key_label} · 每次作答"
    body = masthead(kicker=kicker, title=title,
                    subtitle="按提交时间倒序 · 判词为模型生成",
                    pager=f"{page} / {pages} 页 · 共 {total} 次")
    if not rows:
        body += '<div class="rows"><div class="row"><p class="comment">没有这一页的记录。</p></div></div>'
        return document(title=title, body=body + footer(commands, notes), width=width)

    cards = []
    for row in rows:
        tone, label = verdict_badge(row.get("verdict"))
        submission = clip(clean_text(row.get("submission") or ""), COMMENT_LIMIT * 3,
                          "…（预览）")
        comment = clip(clean_text(row.get("comment") or ""), COMMENT_LIMIT, "…")
        cards.append(
            f'<div class="row {tone}">'
            f'<div class="top"><span class="badge {tone}">{esc(label)}</span>'
            f'<span class="key">#{esc(row.get("id"))}</span>'
            f'<span class="spacer"></span>'
            f'<span class="tag">{esc(row.get("at") or "")}</span></div>'
            f'<p class="meta">{esc(submission)}</p>'
            f'<p class="comment">{esc(comment)}</p>'
            f'<p class="actions">完整作答与判词 '
            f'<span class="cmd">{esc(row.get("detail_cmd") or "")}</span></p></div>'
        )
    body += f'<div class="rows">{"".join(cards)}</div>'
    return document(title=title, body=body + footer(commands, notes), width=width)


def entry_detail_card(
    *,
    title: str,
    kicker: str,
    subtitle: str,
    pager: str,
    verdict: str,
    points_label: str,
    points_note: str,
    hint_label: str,
    hint_note: str,
    submission: str,
    comment: str,
    counterexample: str = "",
    commands: Sequence[tuple[str, str]] = (),
    notes: Iterable[str] = (),
    width: int = CARD_WIDTH,
) -> str:
    """单条提交的完整回看（``history show 提交编号``）。"""
    tone, label = verdict_badge(verdict)
    body = masthead(kicker=kicker, title=title, subtitle=subtitle, pager=pager)
    body += (
        f'<div class="verdict {tone}"><span class="badge {tone}">{esc(label)}</span>'
        f'<div class="box"><span class="big">{esc(points_label)}</span>'
        f'<span class="lbl">{esc(points_note)}</span></div>'
        f'<span class="spacer"></span>'
        f'<div class="box" style="text-align:right"><span class="big">{esc(hint_label)}</span>'
        f'<span class="lbl">{esc(hint_note)}</span></div></div>'
    )
    body += section("我的作答", "引用层级已还原") + code_block(submission)
    if str(comment or "").strip():
        body += section("判词") + f'<div class="body">{prose(comment)}</div>'
    if str(counterexample or "").strip():
        body += (section("最小反例", "只给输入与两个结果，不给解法")
                 + f'<div class="callout"><div class="t">COUNTEREXAMPLE</div>'
                   f'<div class="body">{esc(counterexample)}</div></div>')
    return document(title=clean_text(title), body=body + footer(commands, notes), width=width)


def mistakes_card(
    *,
    items: Sequence[dict],
    page: int,
    pages: int,
    total: int,
    due_only: bool = False,
    lane: str = "正题",
    commands: Sequence[tuple[str, str]] = (),
    notes: Iterable[str] = (),
    width: int = CARD_WIDTH,
) -> str:
    """错题本（``mistakes [due] [grm] [页码]``）。

    ``items`` 每项：``key``、``title``（可选）、``fail_count``、``ago``
    （已格式化）、``due``、``last_verdict``、``last_comment``、
    ``redo_cmd``、``look_cmd``。
    """
    kicker = f"口胡 OJ · {lane}错题本"
    title = "错题本 · 待复习" if due_only else "错题本"
    body = masthead(
        kicker=kicker, title=title,
        subtitle="答错或放弃、还没 AC · 最久没管的排最前" if not due_only
        else "超过复习间隔、还没 AC 的题",
        pager=f"{page} / {pages} 页 · 共 {total} 题",
    )
    if not items:
        note = ("目前没有到期的错题，都还没到复习时间。" if due_only
                else "目前没有待复习的错题。答错或放弃的题在题目结束后会进这里，AC 之后自动移出。")
        body += f'<div class="rows"><div class="row"><p class="comment">{esc(note)}</p></div></div>'
        return document(title=clean_text(title), body=body + footer(commands, notes), width=width)

    cards = []
    for item in items:
        verdict = str(item.get("last_verdict") or "")
        tone, label = verdict_badge(verdict)
        comment = clip(clean_text(item.get("last_comment") or ""), COMMENT_LIMIT, "…")
        if verdict.upper() in ("GIVEUP", "GAVE_UP") or not comment:
            # 放弃时的判词是插件自己写的「主动放弃」，没有信息量。
            comment = ("当时直接看了答案，重做一遍才算真的会。"
                       if verdict.upper() in ("GIVEUP", "GAVE_UP") else "（没有留下判词）")
        tags = [f'<span class="tag">未过 {int(item.get("fail_count") or 0)} 次</span>',
                f'<span class="tag">距今 {esc(item.get("ago") or "—")}</span>']
        if item.get("due"):
            tags.append('<span class="tag hot">该复习了</span>')
        title_html = (f'<p class="meta">{esc(item["title"])}</p>' if item.get("title") else "")
        cards.append(
            f'<div class="row {tone}">'
            f'<div class="top"><span class="badge {tone}">{esc(label)}</span>'
            f'<span class="key">{esc(item.get("key") or "未知题号")}</span>'
            f'<span class="spacer"></span>{"".join(tags)}</div>'
            f"{title_html}"
            f'<p class="comment">{esc(comment)}</p>'
            f'<p class="actions">重做 <span class="cmd">{esc(item.get("redo_cmd") or "")}</span>'
            f'<span class="sep">·</span>回看 '
            f'<span class="cmd">{esc(item.get("look_cmd") or "")}</span></p></div>'
        )
    body += f'<div class="rows">{"".join(cards)}</div>'
    return document(title=clean_text(title), body=body + footer(commands, notes), width=width)


def me_card(
    *,
    name: str,
    cells: Sequence[tuple[str, str]],
    hint_rows: Sequence[dict],
    commands: Sequence[tuple[str, str]] = (),
    notes: Iterable[str] = (),
    subtitle: str = "本群统计 · 正题与坑点分别记录",
    width: int = CARD_WIDTH,
) -> str:
    """个人战绩（``me``）。``hint_rows`` 每项：``tone``、``label``、``value``、``note``。"""
    title = f"{name} 的战绩" if name else "我的战绩"
    body = masthead(kicker="口胡 OJ · 战绩", title=title, subtitle=subtitle)
    body += stats(cells, hero=0)
    body += section("已 AC 题目的提示记录")
    cards = []
    for row in hint_rows:
        tone = str(row.get("tone") or "")
        cards.append(
            f'<div class="row {tone}">'
            f'<div class="top"><span class="badge {tone}">{esc(row.get("label"))}</span>'
            f'<span class="spacer"></span>'
            f'<span class="key">{esc(row.get("value"))}</span></div>'
            f'<p class="comment">{esc(row.get("note"))}</p></div>'
        )
    body += f'<div class="rows">{"".join(cards)}</div>'
    return document(title=clean_text(title), body=body + footer(commands, notes), width=width)


def solution_card(
    *,
    key_label: str,
    source: str,
    url: str,
    text: str,
    commands: Sequence[tuple[str, str]] = (),
    notes: Iterable[str] = (),
    width: int = CARD_WIDTH,
) -> str:
    """题后解析（``sol``）：来源必须先于正文，且标明是模型整理。"""
    title = f"{key_label} · 题后解析"
    body = masthead(kicker="口胡 OJ · 题后解析", title=title,
                    subtitle="题目结束后解锁 · 按轮次缓存", pager="来源已标注")
    source_html = esc(source)
    if url:
        source_html += f'<br><span class="link">{esc(url)}</span>'
    body += f'<div class="callout note"><div class="t">来源</div>{source_html}</div>'
    body += section("解析", "基于官方参考整理，可能有误")
    body += f'<div class="body">{prose(text)}</div>'
    return document(title=clean_text(title), body=body + footer(commands, notes), width=width)


# ------------------------------------------------------------------ 渲染入口

def render(html_document: str, *, out_dir: str | None = None,
           width: int = CARD_WIDTH) -> str:
    """把 HTML 文档渲染成 PNG，返回路径。

    真正的浏览器调用在 :mod:`browser_render` 里（单工作线程、每次关浏览器）。
    失败抛 ``RenderError``，调用方必须回退到文字。
    """
    try:
        from .browser_render import render_html  # 延迟导入：本模块可脱离 Playwright 使用
    except ImportError:  # 直接以顶层模块导入时（本地脚本、测试）
        from browser_render import render_html
    return render_html(html_document, out_dir=out_dir, width=width)


__all__ = [
    "CARD_CSS", "CARD_WIDTH", "CARD_ENTRY_PAGE", "CARD_LIST_PAGE",
    "CARD_MISTAKE_PAGE", "CODE_BUDGET", "COMMENT_LIMIT", "DEFAULT_MAX_HEIGHT",
    "VERDICT_TONES", "clean_text", "clip", "code_block", "document", "entries_card",
    "entry_detail_card", "esc", "footer", "history_card", "inline", "masthead",
    "me_card", "mistakes_card", "prose", "render", "section", "solution_card",
    "split_code", "stats", "verdict_badge",
]
