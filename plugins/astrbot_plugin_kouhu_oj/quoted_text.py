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


def _raw_quoted_body(event, own_text: str) -> str:
    """从原始载荷里取出被引用消息的正文，顺带排除本条消息自己的正文。"""
    own = (own_text or '').strip()
    parts = []
    for element in _raw_elements(event):
        content = element.get('content') if isinstance(element, dict) else None
        if not isinstance(content, str):
            continue
        content = content.strip()
        if content and content != own:
            parts.append(content)
    return '\n'.join(parts).strip()


def quote_shape(event) -> str:
    """引用载荷的形状摘要（只报字段名和段数，不碰正文），排查用。"""
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
    return f"message_type={data.get('message_type')!r} keys=[{keys}] elements=[{';'.join(kinds)}]"



def collect_quoted_text(event, sender_id, own_text: str = '') -> tuple[str, str]:
    """收集被引用消息里的文字。

    Args:
        event: AstrBot 事件，暴露 ``get_messages()``。
        sender_id: 当前发消息的人，用来校验引用作者。
        own_text: 本条消息自己的正文，用来在原始载荷里排除它自己。

    Returns:
        ``(文字, 情况)``。情况为 ``''``（没引用文字）、``'self'``（可用）、
        ``'other'``（引用了明确是别人的消息）、``'bot'``（引用了机器人自己的输出）。
        只看浅层引用，嵌套引用一律忽略，和图片收集保持同样的深度。
    """
    messages = getattr(event, 'get_messages', lambda: [])() or []
    collected: list[str] = []
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
                return '', QUOTE_BOT
            collected.append(body)
            state = QUOTE_SELF
            continue
        if author != owner:
            return '', QUOTE_OTHER
        collected.append(body)
        state = QUOTE_SELF
    if collected:
        return '\n'.join(collected).strip(), state

    # 适配器没把引用转成 Reply（QQ 官方群消息常见）：直接读原始载荷。
    body = _raw_quoted_body(event, own_text)
    if not body:
        return '', QUOTE_NONE
    if looks_like_bot_output(body):
        return '', QUOTE_BOT
    return body, QUOTE_SELF
