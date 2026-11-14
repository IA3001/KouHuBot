"""Generate real browser screenshots: python tests/render_preview.py [output directory]."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_kouhu_oj'))
from browser_render import render_statement, render_examples
import record_cards

PROBLEM = {
    'time_limit': 2, 'memory_limit': 256,
    'statement_cn': r'''给定一个由 $$$n$$$ 个非负整数组成的数组 $$$a$$$。

将孤独度定义为满足以下条件的最小正整数 $$$k$$$：任意两个长度为 $$$k$$$ 的连续子数组，其按位或相同。

$$a_i \mid a_{i+1} \mid \cdots \mid a_{i+k-1} = a_j \mid a_{j+1} \mid \cdots \mid a_{j+k-1}$$

其中 $$$\mid$$$ 表示按位或。请计算最小的 $$$k$$$。

【输入】

第一行包含测试用例数 $$$t$$$（$$$1\le t\le 10^4$$$）。

每组包含长度 $$$n$$$ 和数组元素，满足 $$$0\le a_i<2^{20}$$$。

【输出】

输出数组的孤独度。

【公式检查】

分式 $$$\frac{a+b}{2}$$$，求和 $$$\sum_{i=1}^{n}a_i$$$，绝对值 $$$|i-j|\le K$$$。

错误公式只局部降级：$$$\badcommand{x}$$$。后面的正文应正常显示。''',
    'examples': [{'input': '2\n1\n0\n3\n2 2 2', 'output': '1\n1'}],
}
CARDS = {
    'history': lambda: record_cards.history_card(
        rows=[{'key': '628B', 'verdicts': ['AC', 'WA'], 'count': '2 次记录',
               'at': '07-12 21:40', 'points': '+220分', 'hint': '未主动请求提示',
               'title': 'Distinct Characters Queries'},
              {'key': '进行中（结束后揭晓）', 'hidden': True, 'verdicts': ['WA'],
               'count': '1 次记录', 'at': '07-11 18:40', 'points': '—',
               'hint': '主动请求提示2级'}],
        page=1, pages=2, total=7, commands=[('下一页', 'history 2')],
        notes=['提示群内公开，未主动请求不代表独立完成。']),
    'mistakes': lambda: record_cards.mistakes_card(
        items=[{'key': '628B', 'title': 'Distinct Characters Queries', 'fail_count': 2,
                'ago': '3 天', 'due': True, 'last_verdict': 'WA',
                'last_comment': '只统计了位置不同的字符数。',
                'redo_cmd': 'new CF628B', 'look_cmd': 'history show 628B'}],
        page=1, pages=1, total=1),
    'me': lambda: record_cards.me_card(
        name='Alice',
        cells=[('310', '累计积分'), ('4', '解题'), ('75%', '首次通过率'),
               ('9', '提交'), ('1', '放弃')],
        hint_rows=[{'tone': 'ac', 'label': '主动请求提示后 AC', 'value': '2 次',
                    'note': '判词里保留了当时请求的最高提示级别。'},
                   {'tone': '', 'label': '未主动请求提示', 'value': '1 次',
                    'note': '提示群内公开，未主动请求不代表独立完成。'}]),
    'detail': lambda: record_cards.entry_detail_card(
        title='记录 #41 · 628B', kicker='口胡 OJ · 正题作答',
        subtitle='2026-07-12 21:40', pager='作答 1/1', verdict='WA',
        points_label='0 分', points_note='本次得分',
        hint_label='主动请求提示1级', hint_note='请求的最高提示级别',
        submission='for (int i = 0; i < n; i++) if (s[i] != t[i]) cnt++;',
        comment='## 主要问题\n- 没有考虑 `k` 次交换能修正的字符对。',
        counterexample='输入：3\nabc\nbca\n输出 0，正确 1'),
    'sol': lambda: record_cards.solution_card(
        key_label='628B', source='CF官方题解参考 · 模型中文整理',
        url='https://codeforces.com/blog/entry/12345',
        text='## 关键观察\n- 用 26 个有序集合维护位置。\n\n## 复杂度\n- O(26 n log n)'),
}

if __name__ == '__main__':
    directory = sys.argv[1] if len(sys.argv) > 1 else '.local/render-preview'
    print(render_statement(PROBLEM, directory, 980))
    print(render_examples(PROBLEM, directory, 980))
    # 记录卡片：history / mistakes / me / 提交详情 / 解析
    for name, build in CARDS.items():
        print(record_cards.render(build(), out_dir=directory, width=900))
