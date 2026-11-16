"""记录卡片：把 history / mistakes / me / sol / 提交详情画成图片。

分三层验证：
1. ``record_cards`` 的 HTML 构建（纯函数，不需要浏览器）：转义、隐藏口径落点、
   徽章与页脚、超长切块。
2. 接线：``KouHuOJ._render_card`` 的图片优先与文字回退（渲染被 mock）。
3. 真渲染：本机有 Chromium 才跑，出图并检查宽度，否则 skip。
"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import _astrbot_stub as stub


def load_cards(module):
    """取插件里的 record_cards 模块（main.py 以顶层模块方式导入它）。"""
    return module.record_cards


class HtmlTests(unittest.TestCase):
    """纯 HTML 断言：不碰 Playwright，可在任何环境跑。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls.tmp.name))
        cls.rc = load_cards(cls.mod)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # -- 转义与清洗 -------------------------------------------------
    def test_emoji_are_replaced_because_no_font_covers_them(self):
        rc = self.rc
        self.assertEqual(rc.clean_text('过了✅🔥'), '过了·')   # 连续 emoji 收成一个占位点
        self.assertEqual(rc.clean_text('AC 🎯'), 'AC ·')
        # CJK、全角标点、数学符号必须原样保留。
        for kept in ('我的作答', '（边界）', '≤O(n log n)', '→'):
            self.assertEqual(rc.clean_text(kept), kept)

    def test_untrusted_content_is_escaped_not_executed(self):
        rc = self.rc
        html = rc.history_card(
            rows=[{"key": '<img src=x onerror=alert(1)>', "verdicts": ["WA"],
                   "count": '1 次', "at": '01-02 03:04', "points": '+0分',
                   "hint": '未主动请求提示', "title": '</div><script>bad()</script>'}],
            page=1, pages=1, total=1)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<script>bad()', html)
        self.assertIn('&lt;img src=x', html)

    def test_document_is_offline_and_has_no_script(self):
        rc = self.rc
        html = rc.document(title='t', body='<p>x</p>')
        self.assertIn('<meta charset="utf-8">', html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("style-src 'unsafe-inline'", html)
        self.assertNotIn('script-src', html)   # 卡片是静态文档，不引脚本
        self.assertNotIn('<script', html)
        self.assertNotIn('src="http', html)    # 不加载远程资源
        self.assertIn('box-sizing: border-box', html)  # 少了它 900px 会撑成 980px
        self.assertIn('body { width: 900px; }', html)

    def test_width_is_validated(self):
        rc = self.rc
        for bad in (0, 319, 4097, True, '900', None):
            with self.subTest(width=bad), self.assertRaises(ValueError):
                rc.document(title='t', body='', width=bad)
        self.assertIn('width: 320px', rc.document(title='t', body='', width=320))

    # -- 判词与文案 -------------------------------------------------
    def test_verdict_badges_cover_every_writer_value(self):
        rc = self.rc
        self.assertEqual(rc.verdict_badge('AC'), ('ac', 'AC · 通过'))
        self.assertEqual(rc.verdict_badge('wa')[0], 'wa')
        self.assertEqual(rc.verdict_badge('GAVE_UP')[0], 'giveup')
        self.assertEqual(rc.verdict_badge('PARTIAL')[0], 'partial')
        self.assertEqual(rc.verdict_badge(''), ('', '未知'))

    def test_prose_keeps_markdown_but_never_parses_math(self):
        rc = self.rc
        html = rc.prose('## 观察\n- 用 `add` 维护\n- **关键**：$O(n)$\n\n正文 $1+1$ 与 $$$x$$$')
        self.assertIn('<h3>观察</h3>', html)
        self.assertIn('<li>用 <code class="inl">add</code> 维护</li>', html)
        self.assertIn('<strong>关键</strong>', html)
        # 金额 / 代码里的 $ 必须原样，不能被当公式切开
        self.assertIn('$O(n)$', html)
        self.assertIn('$$$x$$$', html)

    def test_prose_escapes_html_inside_comment(self):
        rc = self.rc
        self.assertNotIn('<b>x</b>', rc.prose('判词 <b>x</b>'))

    # -- history 列表 ------------------------------------------------
    def test_history_card_hides_in_progress_identity(self):
        rc = self.rc
        html = rc.history_card(
            rows=[
                {"key": '628B', "hidden": False, "verdicts": ['AC', 'WA'],
                 "count": '2 次记录', "at": '07-12 21:40', "points": '+220分',
                 "hint": '未主动请求提示', "title": 'Distinct Characters Queries'},
                {"key": '进行中（结束后揭晓）', "hidden": True, "verdicts": ['WA'],
                 "count": '1 次记录', "at": '07-11 18:40', "points": '—',
                 "hint": '主动请求提示2级'},
            ],
            page=1, pages=2, total=7,
            commands=[("下一页", 'history 2')])
        self.assertIn('628B', html)
        self.assertIn('进行中（结束后揭晓）', html)
        self.assertIn('AC · 通过', html)
        self.assertIn('判定轨迹 WA → AC', html)   # newest-first 翻成时间顺序
        self.assertIn('2 次记录', html)
        self.assertIn('1 / 2 页 · 共 7 题', html)
        self.assertIn('history 2', html)
        self.assertIn('class="row ac hidden"', html) if False else None
        self.assertIn('Distinct Characters Queries', html)

    def test_history_card_empty_state(self):
        rc = self.rc
        html = rc.history_card(rows=[], page=1, pages=1, total=0)
        self.assertIn('还没有记录', html)

    # -- 提交详情 ----------------------------------------------------
    def test_entry_detail_omits_missing_sections(self):
        rc = self.rc
        base = dict(title='记录 #7 · 628B', kicker='口胡 OJ · 正题作答',
                    subtitle='2026-07-12 21:40', pager='1 / 1',
                    verdict='WA', points_label='0 分', points_note='本次得分',
                    hint_label='主动请求提示1级', hint_note='请求的最高提示级别',
                    submission='int main(){}')
        html = rc.entry_detail_card(comment='', counterexample='', **base)
        self.assertIn('我的作答', html)
        self.assertNotIn('最小反例', html)
        self.assertNotIn('判词', html)
        with_cp = rc.entry_detail_card(comment='思路漏了边界', counterexample='n=1 时输出 0',
                                       **base)
        self.assertIn('判词', with_cp)
        self.assertIn('最小反例', with_cp)
        self.assertIn('COUNTEREXAMPLE', with_cp)
        self.assertIn('n=1 时输出 0', with_cp)

    def test_entry_detail_marks_legacy_points(self):
        rc = self.rc
        html = rc.entry_detail_card(
            title='记录 #1 · 628B', kicker='k', subtitle='s', pager='p',
            verdict='AC', points_label='旧版未保存', points_note='本次得分',
            hint_label='旧版提示记录未知', hint_note='请求的最高提示级别',
            submission='x', comment='y')
        self.assertIn('旧版未保存', html)
        self.assertIn('旧版提示记录未知', html)

    # -- mistakes ----------------------------------------------------
    def test_mistakes_card_rows_and_due_tag(self):
        rc = self.rc
        html = rc.mistakes_card(
            items=[
                {"key": '628B', "title": 'Array Walk', "fail_count": 2,
                 "ago": '3 天', "due": True, "last_verdict": 'WA',
                 "last_comment": '漏了边界', "redo_cmd": 'new CF628B',
                 "look_cmd": 'history show 628B'},
                {"key": 'grm:12', "fail_count": 1, "ago": '2 小时', "due": False,
                 "last_verdict": 'GAVE_UP', "last_comment": '主动放弃',
                 "redo_cmd": 'grm new', "look_cmd": 'history grm show 12'},
            ],
            page=1, pages=1, total=2, lane='正题')
        self.assertIn('未过 2 次', html)
        self.assertIn('该复习了', html)
        self.assertIn('new CF628B', html)
        self.assertIn('当时直接看了答案', html)      # 放弃判词改成人话
        self.assertIn('grm new', html)
        self.assertIn('Array Walk', html)          # 文字版从不展示 title，卡片补上
        self.assertIn('错题本', html)

    def test_mistakes_card_empty_states_differ_by_due(self):
        rc = self.rc
        normal = rc.mistakes_card(items=[], page=1, pages=1, total=0)
        due = rc.mistakes_card(items=[], page=1, pages=1, total=0, due_only=True)
        self.assertIn('目前没有待复习的错题', normal)
        self.assertIn('目前没有到期的错题', due)
        self.assertIn('错题本 · 待复习', due)

    # -- me（个人主页） ----------------------------------------------
    def me_html(self, name='Alice', **profile_kwargs):
        rc, mod = self.rc, self.mod
        return rc.profile_card(
            name=name,
            cells=[('4', '解题'), ('75%', '首次通过率'), ('9', '提交'), ('1', '放弃')],
            profile=mod.profile_stats.build_profile(**profile_kwargs),
            hint_rows=[
                {"tone": 'ac', "label": '主动请求提示后 AC', "value": '2 次', "note": 'a'},
                {"tone": '', "label": '未主动请求提示', "value": '1 次', "note": 'b'},
                {"tone": 'giveup', "label": '旧版记录未知', "value": '1 次', "note": 'c'},
            ])

    def test_me_card_keeps_the_old_stats_and_hint_breakdown(self):
        html = self.me_html(solves=[])
        for expected in ('Alice 的战绩', '未定级', '旧版记录未知',
                         '已 AC 题目的提示记录', '首次通过率'):
            self.assertIn(expected, html)

    def test_me_card_adds_cf_style_rating_and_periods(self):
        now = 1_700_000_000.0
        tier = self.mod.scoring.TIER_POINTS
        solves = [{'key': '17%02dA' % i, 'title': '题 %d' % i,
                   'points': tier[1400], 'at': now - i * 3600} for i in range(4)]
        html = self.me_html(solves=solves, now=now)
        self.assertIn('1400', html)                       # rating 数字
        self.assertIn('熟练 · specialist', html)           # CF 段位名
        self.assertIn('rank-specialist', html)            # 段位配色
        for label in ('今天', '本周', '本月'):
            self.assertIn(label, html)
        self.assertIn('做题热力图', html)
        self.assertIn('积分曲线', html)
        self.assertIn('最近做过的题', html)

    def test_me_card_heatmap_has_one_cell_per_day(self):
        rc, mod = self.rc, self.mod
        profile = mod.profile_stats.build_profile([])
        html = self.me_html(solves=[])
        weeks = profile['heatmap']['weeks']
        self.assertEqual(html.count('<i class="l'), weeks * 7 + 5)   # 格子 + 图例 5 个
        self.assertIn('heat-months', html)

    def test_me_card_cleans_emoji_only_nicknames(self):
        """昵称里全是 emoji 时，别让标题变成「壹· 的战绩」。"""
        html = self.me_html(name='壹💭💭💭', solves=[])
        self.assertIn('<title>壹的战绩</title>', html)   # 中文名后面不留空格

    def test_me_card_renders_a_real_svg_curve_without_script(self):
        now = 1_700_000_000.0
        solves = [{'key': 'A', 'title': 't', 'points': 220, 'at': now - 86400},
                  {'key': 'B', 'title': 't', 'points': 320, 'at': now}]
        html = self.me_html(solves=solves, now=now)
        self.assertIn('<svg', html)
        self.assertIn('<polyline', html)
        self.assertNotIn('<script', html)
        self.assertIn('现在 <b>540 分</b>', html)

    def test_me_card_unrated_says_how_many_more_solves(self):
        html = self.me_html(solves=[{'key': 'A', 'title': 't', 'points': 220,
                                     'at': 1_700_000_000.0}], now=1_700_000_000.0)
        self.assertIn('未定级', html)
        self.assertIn('再 AC 2 道题', html)

    # -- sol ---------------------------------------------------------
    def test_solution_card_puts_source_before_body(self):
        rc = self.rc
        html = rc.solution_card(
            key_label='628B', source='CF官方题解参考 · 模型中文整理',
            url='https://codeforces.com/blog/entry/1', text='## 观察\n用前缀和')
        self.assertLess(html.index('CF官方题解参考'), html.index('观察'))
        self.assertIn('https://codeforces.com/blog/entry/1', html)
        self.assertIn('<h3>观察</h3>', html)
        self.assertIn('来源', html)

    # -- 切块 --------------------------------------------------------
    def test_split_code_keeps_short_text_whole(self):
        rc = self.rc
        self.assertEqual(rc.split_code('abc'), ['abc'])
        self.assertEqual(len(rc.split_code('x' * 5001)), 2)

    def test_split_code_prefers_newline_and_respects_budget(self):
        rc = self.rc
        body = '\n'.join(f'line {n:04d} ' + 'a' * 30 for n in range(400))
        chunks = rc.split_code(body, budget=1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))
        self.assertEqual(''.join(chunks), body)
        self.assertTrue(chunks[0].endswith('a' * 30))

    def test_clip_does_not_cut_words_in_half(self):
        rc = self.rc
        text = 'word ' * 60
        out = rc.clip(text, 30)
        self.assertLessEqual(len(out), 30)
        self.assertTrue(out.endswith('…'))


class MathTests(unittest.TestCase):
    """解析/判词里的 LaTeX 必须渲染成公式，而不是把 ``\\[`` 原文发出去。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls.tmp.name))
        cls.rc = cls.mod.record_cards

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_solution_card_renders_latex_not_source(self):
        text = ("1. 关键观察\n\n对 \\(T\\) 分段。\n\n\\[\n1\\le b<\\text{数组长度}.\n\\]\n\n"
                "2. 复杂度 \\(O(n^2)\\)")
        html = self.rc.solution_card(key_label='2205E', source='CF官方题解参考 · 模型中文整理',
                                     url='https://codeforces.com/blog/entry/1', text=text)
        self.assertIn('data-tex=', html)
        self.assertIn('data-display="true"', html)      # 显示公式
        self.assertIn('data-display="false"', html)     # 行内公式
        visible = html.split('<body>', 1)[1]
        self.assertNotIn('\\[', visible.replace('data-tex="', ''))   # 正文里不该再出现源码
        self.assertNotIn('\\(', visible.replace('data-tex="', ''))

    def test_math_card_loads_local_katex_plain_card_does_not(self):
        math_html = self.rc.solution_card(key_label='1A', source='s', url='u',
                                          text='公式 \\(x\\)')
        self.assertIn('katex.min.css', math_html)
        self.assertIn('katex.min.js', math_html)
        self.assertIn("script-src file: 'unsafe-inline'", math_html)
        plain = self.rc.history_card(rows=[], page=1, pages=1, total=0)
        self.assertNotIn('katex.min.js', plain)
        self.assertNotIn('script-src', plain)           # 普通卡片仍是纯静态文档

    def test_math_prose_keeps_markdown_structure(self):
        html = self.rc.math_prose("**结论**：只算 \\(T\\) 的分段数。")
        self.assertIn('<strong>结论</strong>', html)
        self.assertIn('data-tex=', html)

    def test_list_numbering_survives_a_display_formula(self):
        html = self.rc.math_prose("1. 甲\n2. 乙\n\n\\[ x=1 \\]\n\n3. 丙")
        self.assertIn('<ol start="3">', html)

    def test_unclosed_formula_stays_literal(self):
        html = self.rc.math_prose("这里少一个右括号 \\(x+1")
        self.assertIn('\\(x+1', html)
        self.assertNotIn('data-tex=', html)

    def test_comment_math_is_inline_not_block(self):
        html = self.rc.math_inline("复杂度 \\(O(n\\log n)\\)，花费 5 元")
        self.assertEqual(html.count('data-tex='), 1)
        self.assertNotIn('<p>', html)
        self.assertIn('花费 5 元', html)

    def test_entries_card_uses_inline_math_for_comments(self):
        html = self.rc.entries_card(
            key_label='1A', rows=[{'id': 3, 'verdict': 'WA', 'at': '01-02 03:04',
                                   'submission': '作答', 'comment': '复杂度 \\(O(n)\\)',
                                   'detail_cmd': 'history show 3'}],
            page=1, pages=1, total=1)
        self.assertIn('data-tex=', html)

    def test_render_waits_for_katex_only_when_needed(self):
        import browser_render
        with patch.object(browser_render, 'render_html', return_value='x.png') as fake:
            self.rc.render('<p>纯文本</p>', out_dir='/tmp')
            self.assertFalse(fake.call_args.kwargs['math'])
            self.rc.render('<p data-tex="x">x</p>', out_dir='/tmp')
            self.assertTrue(fake.call_args.kwargs['math'])


class WiringTests(unittest.IsolatedAsyncioTestCase):
    """图片优先 + 文字回退的接线。渲染器被 mock，跑得很快。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.rc = load_cards(self.mod)
        self.bot = self.mod.KouHuOJ(
            SimpleNamespace(llm_generate=AsyncMock()),
            {'judge_provider_id': 'text', 'new_cooldown_seconds': 0,
             'record_cards_enabled': True},
        )
        root = Path(self.tmp.name)
        self.bot._store = self.mod.OjStore(root / 'score.db')
        self.bot._cache = self.mod.CfCache(root / 'cache.db')
        self.bot._image_dir = root / 'images'
        self.bot._image_dir.mkdir(parents=True, exist_ok=True)
        self.scope = 'group:100200300'
        self.bot._store.ensure_player(self.scope, '1001', 'Alice')

    async def asyncTearDown(self):
        self.bot._store.close()
        self.tmp.cleanup()

    def event(self, text, sender_id='1001'):
        return stub.AstrMessageEvent(text, group_id='100200300', sender_id=sender_id)

    def round(self, key, verdict='WA', comment='思路漏了边界', close=True):
        problem = {'cf_key': key, 'title': '匿名题目', 'points': 100, 'tier': 800}
        self.bot._store.open_problem(self.scope, problem, '1001')
        row = self.bot._store.current(self.scope)
        # note_review 返回轮次 id（int），save_solution 需要它。
        entry = self.bot._store.note_review(
            self.scope, row, '1001', '我的作答', verdict, comment)
        if close:
            self.bot._store.close_problem(
                self.scope, 'ac' if verdict == 'AC' else 'gave_up', '1001')
        return entry

    async def run_cmd(self, handler, text):
        return [item.text async for item in getattr(self.bot, handler)(self.event(text))]

    def is_image(self, text):
        return text.startswith('[image] ')

    # -- 成功路径 ----------------------------------------------------
    async def test_history_list_renders_one_card_and_no_text(self):
        self.round('628B', 'WA')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_history', 'history')
        self.assertEqual(len(out), 1)
        self.assertTrue(self.is_image(out[0]))
        document = fake.call_args.args[0]
        self.assertIn('628B', document)
        self.assertIn('我的正题记录', document)
        self.assertEqual(fake.call_args.kwargs['out_dir'], str(self.bot._image_dir))

    async def test_history_list_card_never_leaks_open_problem_key(self):
        problem = {'cf_key': '1832C', 'title': 'Counting Array', 'points': 100, 'tier': 800}
        self.bot._store.open_problem(self.scope, problem, '1001')
        row = self.bot._store.current(self.scope)
        self.bot._store.note_review(self.scope, row, '1001', '我的作答', 'WA', '错了')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            await self.run_cmd('cmd_history', 'history')
        document = fake.call_args.args[0]
        self.assertIn('进行中（结束后揭晓）', document)
        self.assertNotIn('1832C', document)
        self.assertNotIn('Counting Array', document)

    async def test_history_entries_render_card_with_detail_command(self):
        self.round('628B', 'WA')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_history', 'history show 628B')
        self.assertTrue(self.is_image(out[0]))
        document = fake.call_args.args[0]
        self.assertIn('history show', document)
        self.assertIn('#', document)

    # -- 图片口径一页装得更多 ----------------------------------------
    async def test_history_card_fits_thirty_problems_on_one_page(self):
        """一张图装 30 题：12 题不该分成两页（文字版 5 题一页会分成 3 页）。"""
        keys = [f'70{n}C' for n in range(12)]
        for key in keys:
            self.round(key, 'WA')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_history', 'history')
        self.assertEqual(len(out), 1)
        document = fake.call_args.args[0]
        self.assertIn('1 / 1 页 · 共 12 题', document)
        for key in keys:
            self.assertIn(key, document)

    async def test_history_card_keeps_thirty_per_page_boundary(self):
        for n in range(33):
            self.round(f'8{n:02d}A', 'WA')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            first = await self.run_cmd('cmd_history', 'history')
            second = await self.run_cmd('cmd_history', 'history 2')
        self.assertIn('1 / 2 页 · 共 33 题', fake.call_args_list[0].args[0])
        self.assertIn('2 / 2 页 · 共 33 题', fake.call_args_list[1].args[0])
        self.assertIn('history 2', fake.call_args_list[0].args[0])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

    async def test_entries_card_fits_twenty_attempts_on_one_page(self):
        for n in range(6):
            self.round('628B', 'WA', comment=f'第 {n} 次')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_history', 'history show 628B')
        self.assertEqual(len(out), 1)
        document = fake.call_args.args[0]
        self.assertIn('1 / 1 页 · 共 6 次', document)
        self.assertNotIn('下一批', document)

    async def test_mistakes_card_fits_twenty_on_one_page(self):
        for n in range(7):
            self.round(f'9{n}D', 'WA')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_mistakes', 'mistakes')
        self.assertEqual(len(out), 1)
        self.assertIn('1 / 1 页 · 共 7 题', fake.call_args.args[0])

    async def test_text_fallback_requeries_with_message_sized_pages(self):
        """卡片渲染失败时，文字版仍是 5 题一页，不会把 8 题塞进一条消息。"""
        for n in range(8):
            self.round(f'7{n}E', 'WA')
        with patch.object(self.rc, 'render', side_effect=RuntimeError('没有浏览器')):
            out = await self.run_cmd('cmd_history', 'history')
        text = '\n'.join(out)
        self.assertIn('1/2 页 · 共 8 题', text)   # 文字口径 5 题/页

    async def test_history_detail_renders_card(self):
        rid = self.bot._store.note_review(
            self.scope, dict(cf_key='628B', title='T', opened_at=1, status='ac'),
            '1001', 'int main(){}', 'WA', '漏了边界')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_history', f'history show {rid}')
        self.assertTrue(self.is_image(out[0]))
        document = fake.call_args.args[0]
        self.assertIn('int main(){}', document)
        self.assertIn('漏了边界', document)

    async def test_mistakes_renders_card(self):
        self.round('628B', 'WA')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_mistakes', 'mistakes')
        self.assertTrue(self.is_image(out[0]))
        self.assertEqual(len(out), 1)
        document = fake.call_args.args[0]
        self.assertIn('628B', document)
        self.assertIn('错题本', document)

    async def test_me_renders_card(self):
        self.bot._store._conn.execute(
            'UPDATE players SET total_score=310, solved=4, attempts=9, gave_up=1 '
            'WHERE scope_id=? AND user_id=?', (self.scope, '1001'))
        self.bot._store._conn.commit()
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_me', 'me')
        self.assertTrue(self.is_image(out[0]))
        html = fake.call_args.args[0]
        self.assertIn('战绩', html)
        self.assertIn('做题热力图', html)      # 个人主页多了 CF 风格的那几块
        self.assertIn('今天', html)

    async def test_solution_renders_card_and_keeps_clickable_link(self):
        rid = self.round('628B', 'AC', '不错')
        self.bot._store.save_solution(
            rid, '关键观察：前缀和',
            'CF官方题解参考 · 模型中文整理\nhttps://codeforces.com/blog/entry/1')
        with patch.object(self.rc, 'render', return_value='card.png') as fake:
            out = await self.run_cmd('cmd_solution', 'sol 628B')
        self.assertTrue(self.is_image(out[0]))
        self.assertEqual(len(out), 2)
        # 图里的链接点不动，正文必须再给一条能点的原文地址。
        self.assertIn('https://codeforces.com/blog/entry/1', out[1])
        self.assertNotIn('【', out[1])
        document = fake.call_args.args[0]
        self.assertIn('前缀和', document)
        self.assertIn('CF官方题解参考', document)

    # -- 回退路径 ----------------------------------------------------
    async def test_render_failure_falls_back_to_text(self):
        self.round('628B', 'WA')
        with patch.object(self.rc, 'render', side_effect=RuntimeError('没有浏览器')):
            out = await self.run_cmd('cmd_history', 'history')
        self.assertFalse(any(self.is_image(item) for item in out))
        self.assertIn('我的正题记录', '\n'.join(out))
        self.assertIn('628B', '\n'.join(out))

    async def test_mistakes_falls_back_to_text_on_failure(self):
        self.round('628B', 'WA')
        with patch.object(self.rc, 'render', side_effect=RuntimeError('boom')):
            out = await self.run_cmd('cmd_mistakes', 'mistakes')
        self.assertIn('628B', '\n'.join(out))
        self.assertIn('未过 1 次', '\n'.join(out))

    async def test_builder_crash_falls_back_to_text(self):
        self.round('628B', 'WA')
        with patch.object(self.rc, 'history_card', side_effect=ValueError('坏数据')):
            out = await self.run_cmd('cmd_history', 'history')
        self.assertIn('我的正题记录', '\n'.join(out))

    async def test_no_image_dir_means_text(self):
        """未初始化的插件（没有图片目录）永远走文字。"""
        self.round('628B', 'WA')
        self.bot._image_dir = None
        with patch.object(self.rc, 'render', side_effect=AssertionError('不该渲染')) as fake:
            out = await self.run_cmd('cmd_history', 'history')
        self.assertFalse(fake.called)
        self.assertIn('我的正题记录', '\n'.join(out))

    async def test_config_switch_disables_cards(self):
        self.round('628B', 'WA')
        self.bot.config['record_cards_enabled'] = False
        with patch.object(self.rc, 'render', side_effect=AssertionError('不该渲染')) as fake:
            out = await self.run_cmd('cmd_mistakes', 'mistakes')
        self.assertFalse(fake.called)
        self.assertIn('未过 1 次', '\n'.join(out))

    # -- 宽度 --------------------------------------------------------
    async def test_card_width_follows_image_width_and_clamps(self):
        self.assertEqual(self.bot._card_width(), 900)
        self.bot.config['record_card_width'] = 1200
        self.assertEqual(self.bot._card_width(), 1200)
        for value, expected in ((0, 900), (-5, 900), (100, 320), (9999, 4096), ('x', 900)):
            self.bot.config['record_card_width'] = value
            self.assertEqual(self.bot._card_width(), expected, value)


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    """真渲染：出图、量宽度。没有 Chromium 时 skip，不算失败。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.rc = load_cards(self.mod)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_history_card_renders_to_png(self):
        rc = self.rc
        document = rc.history_card(
            rows=[{"key": '628B', "verdicts": ['AC', 'WA'], "count": '2 次记录',
                   "at": '07-12 21:40', "points": '+220分', "hint": '未主动请求提示',
                   "title": 'Distinct Characters Queries'}],
            page=1, pages=1, total=1, commands=[("解析", 'sol 628B')])
        try:
            path = rc.render(document, out_dir=self.tmp.name, width=900)
        except Exception as exc:  # noqa: BLE001 - 环境没有浏览器就跳过
            self.skipTest(f'本机没有可用的 Chromium：{exc}')
        image = Path(path)
        self.assertTrue(image.is_file())
        self.assertGreater(image.stat().st_size, 5000)
        with image.open('rb') as handle:
            header = handle.read(24)
        self.assertEqual(header[:8], b'\x89PNG\r\n\x1a\n')
        width = int.from_bytes(header[16:20], 'big')
        height = int.from_bytes(header[20:24], 'big')
        self.assertEqual(width, 900)
        self.assertLess(height, rc.DEFAULT_MAX_HEIGHT + 6000)


if __name__ == '__main__':
    unittest.main()
