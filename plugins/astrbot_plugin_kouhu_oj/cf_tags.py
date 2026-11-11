"""Parse CF's live tag names without a fixed allow-list."""
import re

ALIASES = {
    '动态规划':'dp','贪心':'greedy','数学':'math','实现':'implementation',
    '暴力':'brute force','二分':'binary search','排序':'sortings',
    '图论':'graphs','树':'trees','数据结构':'data structures','字符串':'strings',
    '数论':'number theory','组合数学':'combinatorics','几何':'geometry',
    '计算几何':'geometry','位运算':'bitmasks','双指针':'two pointers',
    '构造':'constructive algorithms','最短路':'shortest paths','并查集':'dsu',
    '搜索':'dfs and similar','哈希':'hashing','概率':'probabilities',
    '博弈':'games','网络流':'flows','矩阵':'matrices','分治':'divide and conquer',
    '交互':'interactive','FFT':'fft','字符串后缀结构':'string suffix structures',
    '表达式解析':'expression parsing','三分':'ternary search','图匹配':'graph matchings',
    '中国剩余定理':'chinese remainder theorem','2-sat':'2-sat',
    'Meet-in-the-middle':'meet-in-the-middle','调度':'schedules',
}
DIVISIONS={'1':(1900,3500),'2':(1400,2100),'3':(1000,1600),'4':(800,1200)}

def parse_tag_query(text, available):
    """Return AND tags and optional range; multiword names stay intact."""
    query=text.strip()
    bounds=None
    suffix=re.search(r'(?:^|\s)(\[?\s*\d{1,4}\s*-\s*\d{1,4}\s*\]?|[1-4])\s*$',query)
    if suffix:
        raw=suffix.group(1).strip()
        if raw in DIVISIONS:
            bounds=DIVISIONS[raw]
        else:
            match=re.fullmatch(r'\[?\s*(\d+)\s*-\s*(\d+)\s*\]?',raw)
            low,high=map(int,match.groups())
            if not 800<=low<=high<=3500:
                raise ValueError('rating范围须满足800 ≤ 左端 ≤ 右端 ≤ 3500。')
            bounds=(low,high)
        query=query[:suffix.start()].strip()
    names={tag.casefold():tag for tag in available}
    aliases={key.casefold():value for key,value in ALIASES.items()}
    tags=[]
    for part in re.split(r'\s*[+,，＋]\s*',query):
        key=part.strip().casefold()
        canonical=aliases.get(key,key)
        tag=names.get(canonical.casefold())
        if not tag:
            raise ValueError('未知CF标签：'+(part.strip() or '空标签')+'。发 new tags 查看完整标签；多标签用 + 连接。')
        if tag not in tags:
            tags.append(tag)
    return tags,bounds
