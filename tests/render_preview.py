"""Generate real browser screenshots: python tests/render_preview.py [output directory]."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_kouhu_oj'))
from browser_render import render_statement, render_examples

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
if __name__ == '__main__':
    directory = sys.argv[1] if len(sys.argv) > 1 else '.local/render-preview'
    print(render_statement(PROBLEM, directory, 980))
    print(render_examples(PROBLEM, directory, 980))
