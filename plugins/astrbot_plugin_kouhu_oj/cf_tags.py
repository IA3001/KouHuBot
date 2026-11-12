"""把群友的中文/英文写法解析成 CF 当前题库里的真实标签。

CF 的标签是动态的，所以这里**只用别名做输入归一化**，不维护英文白名单：
- 英文：大小写、单复数、连字符/空格互换、常见缩写都接受；
- 中文：常用别名 + 没有对应 CF 标签时的“相近标签”说明；
- 标点：全角/半角、中文顿号、波浪号、en dash、`1200到1600` 都当成区间；
- 找不到时给出相近标签，而不是只说“未知”。
"""
from __future__ import annotations

import difflib
import re
import unicodedata

DIVISIONS = {'1': (1900, 3500), '2': (1400, 2100), '3': (1000, 1600), '4': (800, 1200)}
RATING_MIN, RATING_MAX = 800, 3500
MAX_ECHO = 40

# 别名 -> 一个标签或一组标签（多标签表示“同时满足”）。
# 键会在加载时做 NFKC + casefold，所以英文大小写、全角写法都不必重复列。
ALIASES: dict[str, str | list[str]] = {
    # 中文常用名
    '动态规划': 'dp', '动规': 'dp',
    '贪心': 'greedy',
    '数学': 'math',
    '实现': 'implementation', '模拟': 'implementation',
    '暴力': 'brute force', '枚举': 'brute force', '暴力枚举': 'brute force',
    '二分': 'binary search', '二分查找': 'binary search', '二分答案': 'binary search',
    '排序': 'sortings',
    '图论': 'graphs', '图': 'graphs',
    '树': 'trees',
    '数据结构': 'data structures',
    '字符串': 'strings',
    '数论': 'number theory',
    '组合数学': 'combinatorics', '组合': 'combinatorics',
    '几何': 'geometry', '计算几何': 'geometry',
    '位运算': 'bitmasks', '位掩码': 'bitmasks',
    '状压': 'bitmasks', '状态压缩': 'bitmasks',
    '双指针': 'two pointers',
    '构造': 'constructive algorithms', '构造题': 'constructive algorithms',
    '最短路': 'shortest paths',
    '并查集': 'dsu',
    '搜索': 'dfs and similar', '深搜': 'dfs and similar',
    '哈希': 'hashing', '散列': 'hashing',
    '概率': 'probabilities', '概率期望': 'probabilities', '期望': 'probabilities',
    '博弈': 'games', '博弈论': 'games',
    '网络流': 'flows', '最大流': 'flows', '费用流': 'flows',
    '矩阵': 'matrices', 'matrix': 'matrices',
    '分治': 'divide and conquer',
    '交互': 'interactive', '交互题': 'interactive',
    '快速傅里叶变换': 'fft',
    '字符串后缀结构': 'string suffix structures',
    '后缀数组': 'string suffix structures', '后缀自动机': 'string suffix structures',
    '表达式解析': 'expression parsing',
    '三分': 'ternary search',
    '图匹配': 'graph matchings', '二分图匹配': 'graph matchings',
    '中国剩余定理': 'chinese remainder theorem',
    '折半搜索': 'meet-in-the-middle', '折半枚举': 'meet-in-the-middle',
    '调度': 'schedules',
    '通信': 'communication', '通信题': 'communication',
    # 英文缩写 / 常用写法
    'dfs': 'dfs and similar',
    '2sat': '2-sat',
    'binarysearch': 'binary search',
    'shortestpath': 'shortest paths',
    'twopointers': 'two pointers',
    'datastructures': 'data structures',
    'suffixarray': 'string suffix structures',
}

# 这些中文词在 CF 没有同名标签，按最接近的标签抽题并明确说明，
# 避免只报“未知CF标签”让群友以为功能坏了。
CONCEPTS: dict[str, tuple[list[str], str]] = {
    '线段树': (['data structures'], 'CF没有线段树标签，已按 data structures 抽题'),
    '树状数组': (['data structures'], 'CF没有树状数组标签，已按 data structures 抽题'),
    '前缀和': (['data structures'], 'CF没有前缀和标签，已按 data structures 抽题'),
    '单调栈': (['data structures'], 'CF没有单调栈标签，已按 data structures 抽题'),
    '单调队列': (['data structures'], 'CF没有单调队列标签，已按 data structures 抽题'),
    '平衡树': (['data structures'], 'CF没有平衡树标签，已按 data structures 抽题'),
    '拓扑排序': (['graphs'], 'CF没有拓扑排序标签，已按 graphs 抽题'),
    '树形dp': (['dp', 'trees'], 'CF没有树形dp标签，已按 dp+trees 抽题'),
    '树上dp': (['dp', 'trees'], 'CF没有树形dp标签，已按 dp+trees 抽题'),
    '数位dp': (['dp'], 'CF没有数位dp标签，已按 dp 抽题'),
    '状压dp': (['dp', 'bitmasks'], 'CF没有状压dp标签，已按 dp+bitmasks 抽题'),
    '矩阵快速幂': (['matrices'], 'CF没有矩阵快速幂标签，已按 matrices 抽题'),
    '容斥': (['combinatorics'], 'CF没有容斥标签，已按 combinatorics 抽题'),
    '广搜': (['graphs'], 'CF没有BFS标签，已按 graphs 抽题'),
    'lca': (['trees'], 'CF没有LCA标签，已按 trees 抽题'),
    'segtree': (['data structures'], 'CF没有线段树标签，已按 data structures 抽题'),
    'segment tree': (['data structures'], 'CF没有线段树标签，已按 data structures 抽题'),
    'toposort': (['graphs'], 'CF没有拓扑排序标签，已按 graphs 抽题'),
    'topological sort': (['graphs'], 'CF没有拓扑排序标签，已按 graphs 抽题'),
    'prefix sum': (['data structures'], 'CF没有前缀和标签，已按 data structures 抽题'),
    'digit dp': (['dp'], 'CF没有数位dp标签，已按 dp 抽题'),
    'tree dp': (['dp', 'trees'], 'CF没有树形dp标签，已按 dp+trees 抽题'),
    'bitmask dp': (['dp', 'bitmasks'], 'CF没有状压dp标签，已按 dp+bitmasks 抽题'),
    'matrix exponentiation': (['matrices'], 'CF没有矩阵快速幂标签，已按 matrices 抽题'),
    'bfs': (['graphs'], 'CF没有BFS标签，已按 graphs 抽题'),
    'ntt': (['fft'], 'CF没有NTT标签，已按 fft 抽题'),
}


class TagQueryError(ValueError):
    """可直接展示给群友的解析错误。"""

    def __init__(self, message: str, suggestions: list[str] | None = None) -> None:
        self.message = message
        self.suggestions = suggestions or []
        super().__init__(message)


def normalize(text: str) -> str:
    """把全角/中文标点/各种破折号统一成解析器能处理的半角形式。"""
    text = unicodedata.normalize('NFKC', text or '')
    text = text.replace('\u3000', ' ')
    # NFKC 之后全角 ， ； ／ ＋ 已经变成半角，这里统一成半角逗号/加号。
    text = text.replace('、', ',').replace(';', ',').replace('|', ',').replace('/', ',')
    # 数字之间的 ~ – — − 到 至 都当 rating 区间的连字符。
    text = re.sub(r'(?<=\d)\s*[~\-–—−到至]\s*(?=\d)', '-', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text.strip('。．.！!？?，,;；、+ ')


def _key(text: str) -> str:
    return normalize(text).casefold()


def _singular_forms(word: str) -> list[str]:
    out: list[str] = []
    if word.endswith('ies') and len(word) > 3:
        out.append(word[:-3] + 'y')
    if word.endswith('s') and not word.endswith('ss') and len(word) > 1:
        out.append(word[:-1])
    return out


def _variants(key: str) -> list[str]:
    """英文标签的常见写法：连字符/空格互换、去连字符、单数。"""
    out: list[str] = []

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in out:
            out.append(value)

    add(key)
    add(key.replace('-', ' '))
    add(key.replace(' ', '-'))
    add(key.replace('-', ''))
    add(key.replace(' ', ''))
    words = key.split(' ')
    if words:
        for singular in _singular_forms(words[-1]):
            add(' '.join(words[:-1] + [singular]))
    return out


def _build_index(available) -> tuple[dict, dict]:
    """key -> (标签组, 说明)；display 用于把模糊匹配结果还原成英文标签。"""
    index: dict[str, tuple[tuple[str, ...], str | None]] = {}
    display: dict[str, list[str]] = {}

    def put(key: str, tags: tuple[str, ...], note: str | None = None) -> None:
        if key and key not in index:
            index[key] = (tags, note)
            display.setdefault(key, list(tags))

    for tag in available:
        canonical = _key(tag)
        put(canonical, (tag,))
        display[canonical] = [tag]
        for variant in _variants(canonical):
            put(variant, (tag,))
            display.setdefault(variant, [tag])
    for alias, target in ALIASES.items():
        targets = (target,) if isinstance(target, str) else tuple(target)
        put(_key(alias), targets)
    for alias, (targets, note) in CONCEPTS.items():
        put(_key(alias), tuple(targets), note)
    return index, display


def _lookup(index: dict, text: str):
    for variant in _variants(_key(text)):
        hit = index.get(variant)
        if hit:
            return hit
    return None


def _suggest(raw: str, index: dict, display: dict, limit: int = 3) -> list[str]:
    keys = list(index)
    matches = difflib.get_close_matches(_key(raw), keys, n=12, cutoff=0.55)
    out: list[str] = []
    for match in matches:
        for tag in display.get(match, []):
            if tag not in out:
                out.append(tag)
        if len(out) >= limit:
            break
    return out[:limit]


def _unknown(raw: str, index: dict, display: dict) -> TagQueryError:
    shown = raw if len(raw) <= MAX_ECHO else raw[:MAX_ECHO] + '…'
    suggestions = _suggest(raw, index, display)
    message = f'未知CF标签：{shown}。'
    if suggestions:
        message += '相近标签：' + '、'.join(suggestions) + '。'
    return TagQueryError(message + '发 new tags 查看完整标签；多标签用 + 分隔。')


def _resolve_chunk(chunk: str, index: dict, display: dict):
    hit = _lookup(index, chunk)
    if hit:
        return hit
    words = chunk.split(' ')
    if len(words) > 1:
        tags: list[str] = []
        notes: list[str] = []
        pos = 0
        while pos < len(words):
            matched = None
            for end in range(len(words), pos, -1):
                candidate = ' '.join(words[pos:end])
                found = _lookup(index, candidate)
                if found:
                    matched = (candidate, found)
                    break
            if not matched:
                missing = ' '.join(words[pos:])
                if _NUMBER_ONLY.match(missing):
                    raise TagQueryError(
                        f'{missing} 不是标签。rating 请写成 1200-1600；Div 预设用 1–4。'
                    )
                raise _unknown(missing, index, display)
            candidate, (found_tags, note) = matched
            tags.extend(found_tags)
            if note and note not in notes:
                notes.append(note)
            pos += len(candidate.split(' '))
        return tuple(tags), '；'.join(notes) if notes else None
    raise _unknown(chunk, index, display)


_RANGE_TAIL = re.compile(
    r'(?:(?:^|\s)(?:rating|r)\s*)?'
    r'[\[(]?\s*(\d{3,4})\s*-\s*(\d{3,4})\s*[\])]?\s*(?:分|rating)?\s*$',
    re.I,
)
_DIV_HEAD = re.compile(r'^(?:div|d|难度)\s*([1-4])(?:\s+|$)', re.I)
_DIV_TAIL = re.compile(r'(?:^|\s)(?:div|d|难度)\s*([1-4])\s*$', re.I)
_BARE_DIV = re.compile(r'(?:^|\s)([1-4])\s*$')
_ATTACHED_DIV = re.compile(r'^(.*?)([1-4])$')
_BAD_DIV = re.compile(r'(?:^|\s)(?:div|d|难度)\s*([5-9])\b', re.I)
_NUMBER_ONLY = re.compile(r'^\d+$')


def _set_bounds(bounds, value):
    if bounds is not None and bounds != value:
        raise TagQueryError('只能指定一个难度/rating区间，例如 new dp 2 或 new dp 1200-1600。')
    return value


def parse_tag_query(text: str, available) -> tuple[list[str], tuple[int, int] | None, str]:
    """解析标签查询，返回 (标签列表, rating区间或None, 说明)。"""
    query = normalize(text)
    if not query:
        raise TagQueryError('请给出标签，例如 new dp；发 new tags 查看完整标签。')
    available_keys = {tag for tag in available}
    index, display = _build_index(available)
    bounds: tuple[int, int] | None = None
    notes: list[str] = []

    head = _DIV_HEAD.match(query)
    if head:
        bounds = _set_bounds(bounds, DIVISIONS[head.group(1)])
        query = query[head.end():].strip()

    # 区间和 Div 可能互相排在后面（dp 1200-1600 2 / dp 2 1200-1600），
    # 交替剥两轮，避免只认一种顺序。
    range_set = False
    for _ in range(4):
        before = (query, bounds, range_set)
        # 先剥 Div，再剥 range；两种顺序交替出现在 dp 2 1200-1600 /
        # dp 1200-1600 2 里，所以循环到不再变化。
        tail = _DIV_TAIL.search(query)
        if tail:
            if not range_set:
                bounds = _set_bounds(bounds, DIVISIONS[tail.group(1)])
            query = query[:tail.start()].strip()
        else:
            bare = _BARE_DIV.search(query)
            if bare:
                if not range_set:
                    bounds = _set_bounds(bounds, DIVISIONS[bare.group(1)])
                query = query[:bare.start()].strip()
        range_match = _RANGE_TAIL.search(query)
        if range_match:
            low, high = int(range_match.group(1)), int(range_match.group(2))
            if not RATING_MIN <= low <= high <= RATING_MAX:
                raise TagQueryError('rating范围须满足800 ≤ 左端 ≤ 右端 ≤ 3500。')
            if range_set and bounds != (low, high):
                raise TagQueryError('只能指定一个rating区间。')
            bounds = (low, high)
            range_set = True
            query = query[:range_match.start()].strip()
        if (query, bounds, range_set) == before:
            break

    if _BAD_DIV.search(query):
        raise TagQueryError('Div 只支持 1–4，例如 new dp 2；也可以直接写 rating 区间 1200-1600。')
    if not query:
        raise TagQueryError('请同时给出标签，例如 new dp 1200-1600；发 new tags 查看完整标签。')

    tags: list[str] = []
    for raw_chunk in re.split(r'[+,]', query):
        chunk = raw_chunk.strip()
        if not chunk:
            continue  # dp+ / dp++trees 的空片段直接忽略
        if bounds is None:
            attached = _ATTACHED_DIV.match(chunk)
            if attached and attached.group(1).strip():
                base = attached.group(1).strip()
                if _lookup(index, base):
                    bounds = DIVISIONS[attached.group(2)]
                    chunk = base
        if _NUMBER_ONLY.match(chunk):
            raise TagQueryError(
                f'{chunk} 不是标签。rating 请写成 1200-1600；Div 预设用 1–4。发 new tags 查看完整标签。'
            )
        found_tags, note = _resolve_chunk(chunk, index, display)
        missing = [tag for tag in found_tags if tag not in available_keys]
        if missing:
            raise TagQueryError(
                'CF当前题库没有 ' + '、'.join(missing) + f' 标签（来自“{chunk}”）。'
                '发 new tags 查看当前可用标签。'
            )
        for tag in found_tags:
            if tag not in tags:
                tags.append(tag)
        if note:
            for item in note.split('；'):
                if item and item not in notes:
                    notes.append(item)

    if not tags:
        raise TagQueryError('没有解析出标签；发 new tags 查看完整标签。')
    return tags, bounds, '；'.join(notes)


def chinese_aliases(tag: str) -> list[str]:
    """给 new tags 列表显示用的中文别名（不显示带“CF没有此标签”说明的概念映射）。"""
    out: list[str] = []
    for alias, target in ALIASES.items():
        targets = [target] if isinstance(target, str) else list(target)
        if tag in targets and alias not in CONCEPTS and re.search(r'[\u4e00-\u9fff]', alias):
            if alias not in out:
                out.append(alias)
    return out
