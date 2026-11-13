"""把「引用一条消息再发 sub」变成可用作答。

群里最自然的交卷姿势不是把思路粘在 sub 后面：先写一段分析，再引用它发一个
sub。这类消息的正文只有 ``sub``，思路在被引用的那条消息里。

作者校验分两种情况：

* 平台给了被引用消息的作者（aiocqhttp 等）→ 必须是自己发的。引用别人的内容交卷
  等于替别人得分，白送一次 AC。
* 平台不给作者（QQ 官方机器人只有引用编号和正文，没有作者字段）→ 按可用处理，
  否则这个功能在 QQ 官方机器人上永远不会生效。此时改为挡掉机器人自己的输出：
  引用题面、提示或指令菜单再发 sub，多半是想问别的，不该当成作答送进判题。
"""
from __future__ import annotations

QUOTE_NONE = ''
QUOTE_SELF = 'self'
QUOTE_OTHER = 'other'
QUOTE_BOT = 'bot'

# 平台没给作者时视为「作者未知」的取值。
_UNKNOWN_AUTHORS = ('', '0', 'none')

# 机器人自己发出的消息的辨识特征：题面、提示、菜单、反例都带这些记号。
_BOT_MARKERS = ('口胡 OJ', '📚', '🧩', '📕', '📌', '🎯 最小反例',
                '【这道题】', '【提示】', '【这道坑点】', '› ', '❯ ')


import re

# AstrBot 的 qq_official 适配器把引用（尤其是「引用里还有引用」）摊平成一段
# 带标记的文字再塞进 Reply.chain：``=== 消息 1 ===``、``[消息内容]``、
# ``[消息类型] 引用消息``、``[关联消息]``、``--- 第1条 ---``。
# 这些标记对判题和 history 只会添乱，落库前统一清掉。
_DUMP_HEADER = re.compile(r'^\s*===\s*消息\s*\d+\s*===\s*$', re.M)
_DUMP_KIND = re.compile(r'^\s*\[消息类型\]\s*.*$', re.M)
_DUMP_CONTENT = re.compile(r'^\s*\[消息内容\]\s*', re.M)
_DUMP_ID = re.compile(r'^\s*\[消息(?:ID|id|Id)\]\s*.*$', re.M)
_DUMP_RELATED = re.compile(r'^\s*\[关联消息\]\s*$', re.M)
_DUMP_INDEX = re.compile(r'^\s*---\s*第\s*(\d+)\s*条\s*---\s*$', re.M)
_BLANK_RUN = re.compile(r'\n{3,}')
# 被引用的旧消息是一条完整指令时，行首的 sub / grm sub 是命令本身，不是作答内容。
_LEADING_COMMAND = re.compile(r'^\s*(?:grm\s+)?(?:sub|submit)(?![A-Za-z])[\s:：,，、]*', re.I)
QUOTE_MAX_DEPTH = 4          # 引用最多标四层，再深也不加前缀，免得整段缩进


def parse_quote_blocks(text: str) -> list[tuple[int, str]]:
    """把适配器摊平的引用解析成 [(层级, 正文)]，层级 0 就是被引用的那条。

    适配器给的是一段带标记的文字：``=== 消息 1 ===`` 分出每条消息，
    ``[消息内容]`` 后面是正文，``[关联消息]`` 后面是它自己引用过的消息，
    每条用 ``--- 第N条 ---`` 起头。引用越深，层级越大。
    """
    if not text:
        return []
    blocks: list[list] = []      # [[层级, [行, ...]], ...]
    depth = 0
    child_depth = None
    force_new = False            # 上一条消息结束后必须另起一块，哪怕层级相同
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _DUMP_HEADER.match(line):
            depth, child_depth = 0, None
            force_new = True
            continue
        if _DUMP_KIND.match(line) or _DUMP_ID.match(line):
            continue
        if _DUMP_RELATED.match(line):
            # 后面 ``--- 第N条 ---`` 起头的是这一条的下一层
            child_depth = depth + 1
            continue
        if _DUMP_INDEX.match(line):
            if child_depth is None:
                child_depth = depth + 1
            depth = child_depth
            force_new = True
            continue
        match = _DUMP_CONTENT.match(line)
        content = line[match.end():].strip() if match else line
        if not content:
            continue
        if blocks and blocks[-1][0] == depth and not force_new:
            blocks[-1][1].append(content)
        else:
            blocks.append([depth, [content]])
            force_new = False

    parsed: list[tuple[int, str]] = []
    for depth, lines in blocks:
        lines[0] = _LEADING_COMMAND.sub('', lines[0]).strip() or lines[0]
        parsed.append((depth, '\n'.join(lines)))
    return parsed


def render_plain(blocks: list[tuple[int, str]]) -> str:
    """纯文本：各层按顺序用空行隔开，不带任何符号。给「判断文字内容」的地方用。"""
    return '\n\n'.join(text for _, text in blocks if text.strip()).strip()


def render_markup(blocks: list[tuple[int, str]]) -> str:
    """规范引用格式（Markdown）：第 0 层一个 ``>``，第 1 层 ``> >``，条间留一个 ``>`` 空行。

    用通用符号而不是「（下面是他引用的消息）」这类旁白：这是作答，不是解说。
    """
    rendered: list[str] = []
    for depth, text in blocks:
        if not text.strip():
            continue
        prefix = '> ' * min(depth + 1, QUOTE_MAX_DEPTH)
        rendered.append('\n'.join((prefix + line).rstrip() for line in text.splitlines()))
    return '\n>\n'.join(rendered).strip()


def clean_quote_dump(text: str) -> str:
    """把带标记的引用摊平后清成纯文本（不含引用符号）。"""
    return render_plain(parse_quote_blocks(text))


def _component_text(component) -> str:
    """取一个消息段里的纯文本；图片、@ 等没有 text 的段返回空串。"""
    text = getattr(component, 'text', None)
    return text.strip() if isinstance(text, str) else ''


def _reply_text(reply) -> str:
    """取被引用消息的正文，按段顺序拼接。"""
    parts = []
    for component in getattr(reply, 'chain', None) or ():
        text = _component_text(component)
        if text:
            parts.append(text)
    if parts:
        return '\n'.join(parts)
    fallback = getattr(reply, 'message_str', '') or ''
    return fallback.strip() if isinstance(fallback, str) else ''


def looks_like_bot_output(text: str) -> bool:
    """粗略判断这段文字是不是机器人自己发的（题面/提示/菜单/反例）。"""
    return any(marker in text for marker in _BOT_MARKERS)


def _raw_elements(event) -> list:
    """取 QQ 原始载荷里的 msg_elements。

    AstrBot 的 qq_official 适配器只在 ``message_type == 103`` 时才把引用内容
    转成 Reply.chain；真实群消息里 ``msg_elements`` 存在但 ``message_type``
    不一定给 103，于是引用正文会被整段丢掉。这里直接读原始载荷兜底。
    """
    raw = getattr(getattr(event, 'message_obj', None), 'raw_message', None)
    data = getattr(raw, 'raw_data', None)
    if not isinstance(data, dict):
        return []
    elements = data.get('msg_elements')
    return elements if isinstance(elements, list) else []


def _walk_content(node, path: str, depth: int = 0, out: list | None = None) -> list[tuple[str, str]]:
    """在载荷里找正文类字段（content/text），返回 (路径, 文字) 列表。

    QQ 群消息的引用正文不总在 ``msg_elements[i].content``：真实事件里
    ``msg_elements`` 只给 ``message_type`` 和 ``msg_idx``，被引用的消息本体在
    ``parallel_message`` 里。这两种摆法都要能取到，所以按字段名递归找。
    """
    if out is None:
        out = []
    if depth > 3 or len(out) >= 8:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            child = f'{path}.{key}' if path else str(key)
            if key in ('content', 'text') and isinstance(value, str):
                out.append((child, value.strip()))
            else:
                _walk_content(value, child, depth + 1, out)
    elif isinstance(node, list):
        for index, item in enumerate(node[:4]):
            _walk_content(item, f'{path}[{index}]', depth + 1, out)
    return out


def _raw_quoted_texts(event, own_text: str) -> list[str]:
    """从原始载荷里取出被引用消息的正文（可能多条），排除本条消息自己的正文。"""
    raw = getattr(getattr(event, 'message_obj', None), 'raw_message', None)
    data = getattr(raw, 'raw_data', None)
    if not isinstance(data, dict):
        return []
    own = (own_text or '').strip()
    found: list[str] = []
    for element in _raw_elements(event):
        if isinstance(element, dict) and isinstance(element.get('content'), str):
            found.append(element['content'].strip())
    parallel = data.get('parallel_message')
    if parallel is not None:
        found.extend(text for _, text in _walk_content(parallel, 'parallel_message'))
    return [text for text in found if text and text != own]


def quote_shape(event) -> str:
    """引用载荷的形状摘要（只报字段名、路径和长度，不碰正文），排查用。"""
    raw = getattr(getattr(event, 'message_obj', None), 'raw_message', None)
    data = getattr(raw, 'raw_data', None)
    if not isinstance(data, dict):
        return 'no-raw-data'
    keys = ','.join(sorted(data))[:200]
    elements = data.get('msg_elements')
    kinds = []
    if isinstance(elements, list):
        for element in elements[:2]:
            kinds.append(','.join(sorted(element)) if isinstance(element, dict) else type(element).__name__)
    body = '\n'.join(_raw_quoted_texts(event, getattr(event, 'message_str', '') or ''))
    shape = f"message_type={data.get('message_type')!r} keys=[{keys}] elements=[{';'.join(kinds)}]"
    if body:
        cjk = any('\u3400' <= ch <= '\u9fff' for ch in body)
        shape += f' 取到 len={len(body)} cjk={cjk}'
    else:
        hits = []
        for path, text in _walk_content(data.get('parallel_message'), 'parallel_message'):
            cjk = any('\u3400' <= ch <= '\u9fff' for ch in text)
            hits.append(f'{path}:len={len(text)}:cjk={cjk}')
        shape += f" parallel=[{';'.join(hits)}]"
    return shape




def collect_quoted_blocks(event, sender_id, own_text: str = '') -> tuple[list[tuple[int, str]], str]:
    """收集被引用消息里的文字，按引用层级返回 [(层级, 正文)]。

    Args:
        event: AstrBot 事件，暴露 ``get_messages()``。
        sender_id: 当前发消息的人，用来校验引用作者。
        own_text: 本条消息自己的正文，用来在原始载荷里排除它自己。

    Returns:
        ``(层级块, 情况)``。情况为 ``''``（没引用文字）、``'self'``（可用）、
        ``'other'``（引用了明确是别人的消息）、``'bot'``（引用了机器人自己的输出）。
        层级 0 是被引用的那条，1 是它引用的，以此类推；多选引用会有多个 0。
    """
    messages = getattr(event, 'get_messages', lambda: [])() or []
    blocks: list[tuple[int, str]] = []
    state = QUOTE_NONE
    try:
        from astrbot.core.message.components import Reply
    except ImportError:  # 本地桩没装消息组件时当作没有 Reply 段
        Reply = ()  # type: ignore[assignment]

    owner = str(sender_id)
    for message in messages:
        if not isinstance(message, Reply):
            continue
        body = _reply_text(message)
        if not body:
            continue
        author = str(getattr(message, 'sender_id', '') or '').strip().lower()
        if author in _UNKNOWN_AUTHORS:
            if looks_like_bot_output(body):
                return [], QUOTE_BOT
            blocks.extend(parse_quote_blocks(body))
            state = QUOTE_SELF
            continue
        if author != owner:
            return [], QUOTE_OTHER
        blocks.extend(parse_quote_blocks(body))
        state = QUOTE_SELF
    if blocks:
        return blocks, state

    # 适配器没把引用转成 Reply（QQ 官方群消息常见）：直接读原始载荷。
    texts = _raw_quoted_texts(event, own_text)
    plain = '\n'.join(texts)
    if not plain.strip():
        return [], QUOTE_NONE
    if looks_like_bot_output(plain):
        return [], QUOTE_BOT
    return [(0, text) for text in texts], QUOTE_SELF


def collect_quoted_text(event, sender_id, own_text: str = '') -> tuple[str, str]:
    """只取纯文字（不带引用符号），用于判断内容：是不是指令词、多长、有没有内容。"""
    blocks, state = collect_quoted_blocks(event, sender_id, own_text)
    return render_plain(blocks), state


def collect_quoted_markup(event, sender_id, own_text: str = '') -> tuple[str, str]:
    """取带规范引用符号的版本（> / > >），用于送给评委和落库。"""
    blocks, state = collect_quoted_blocks(event, sender_id, own_text)
    return render_markup(blocks), state
