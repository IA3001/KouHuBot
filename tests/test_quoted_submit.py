"""引用一条消息再发 sub：正文只有 sub，思路在被引用的那条消息里。"""
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import _astrbot_stub as stub


def components():
    from astrbot.core.message.components import At, Image, Plain, Reply
    return At, Image, Plain, Reply


class CollectQuotedTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        sys.path.insert(0, str(stub.PLUGIN_MAIN.parent))
        import quoted_text
        self.helpers = quoted_text

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_the_senders_own_message_counts(self):
        _, _, Plain, Reply = components()
        mine = Reply(chain=[Plain('先二分答案，再检查单调性')], sender_id='1001')
        event = stub.AstrMessageEvent('sub', sender_id='1001', messages=[mine])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001'),
                         ('先二分答案，再检查单调性', self.helpers.QUOTE_SELF))

    def test_quoting_someone_else_is_reported_not_used(self):
        _, _, Plain, Reply = components()
        other = Reply(chain=[Plain('用并查集就行')], sender_id='2002')
        event = stub.AstrMessageEvent('sub', sender_id='1001', messages=[other])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001'),
                         ('', self.helpers.QUOTE_OTHER))

    def test_quoting_the_bot_counts_as_someone_else(self):
        """引用机器人发的题面不能当成自己的作答送进判题。"""
        _, _, Plain, Reply = components()
        bot = Reply(chain=[Plain('题意：求最大子段和')], sender_id='9999')
        event = stub.AstrMessageEvent('sub', sender_id='1001', messages=[bot])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001')[1],
                         self.helpers.QUOTE_OTHER)

    def test_nested_reply_and_non_text_segments_are_ignored(self):
        _, Image, Plain, Reply = components()
        nested = Reply(chain=[Plain('更早的一条')], sender_id='1001')
        reply = Reply(chain=[Plain('结论'), Image(url='https://img/1'), nested], sender_id='1001')
        event = stub.AstrMessageEvent('sub', sender_id='1001', messages=[reply])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001')[0], '结论')

    def test_image_only_quote_yields_no_text(self):
        _, Image, _, Reply = components()
        event = stub.AstrMessageEvent(
            'sub', sender_id='1001', messages=[Reply(chain=[Image(url='https://img/1')], sender_id='1001')])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001'),
                         ('', self.helpers.QUOTE_NONE))

    def test_platform_preview_is_used_when_chain_is_empty(self):
        _, _, _, Reply = components()
        reply = Reply(chain=[], sender_id='1001', message_str='平台给的引用摘要')
        event = stub.AstrMessageEvent('sub', sender_id='1001', messages=[reply])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001')[0], '平台给的引用摘要')

    def test_unknown_author_is_usable_because_qq_official_omits_it(self):
        """QQ 官方机器人的引用没有作者字段，不能因此让功能永远失效。"""
        _, _, Plain, Reply = components()
        reply = Reply(chain=[Plain('先按左端点排序，再贪心覆盖')])  # sender_id 默认 0
        event = stub.AstrMessageEvent('sub', sender_id='1001', messages=[reply])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001'),
                         ('先按左端点排序，再贪心覆盖', self.helpers.QUOTE_SELF))

    def test_unknown_author_quoting_bot_output_is_refused(self):
        _, _, Plain, Reply = components()
        for text in ('📚 口胡 OJ · 指令菜单\n› new · 抽题', '❓ 提示 1\n【提示】想想前缀和',
                     '🎯 最小反例\nn=2, a=[1,1]'):
            event = stub.AstrMessageEvent('sub', sender_id='1001', messages=[Reply(chain=[Plain(text)])])
            self.assertEqual(self.helpers.collect_quoted_text(event, '1001'),
                             ('', self.helpers.QUOTE_BOT), text)

    def test_raw_payload_fallback_when_adapter_skips_the_quote(self):
        """真实群消息里 msg_elements 有正文但 message_type 不给 103，适配器会丢掉它。"""
        from types import SimpleNamespace
        raw = SimpleNamespace(raw_data={
            'id': 'm1', 'content': 'sub',
            'message_reference': {'message_id': 'q1'},
            'msg_elements': [{'content': '暴力枚举所有点对', 'message_type': 103}],
        })
        event = SimpleNamespace(get_messages=lambda: [],
                                message_obj=SimpleNamespace(raw_message=raw),
                                message_str='sub')
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001', 'sub'),
                         ('暴力枚举所有点对', self.helpers.QUOTE_SELF))

    def test_raw_payload_fallback_skips_own_body_and_bot_output(self):
        from types import SimpleNamespace
        own = SimpleNamespace(raw_data={'content': 'sub', 'msg_elements': [{'content': 'sub'}]})
        event = SimpleNamespace(get_messages=lambda: [], message_obj=SimpleNamespace(raw_message=own),
                                message_str='sub')
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001', 'sub'),
                         ('', self.helpers.QUOTE_NONE))

        bot = SimpleNamespace(raw_data={'content': 'sub', 'msg_elements': [{'content': '📚 口胡 OJ · 指令菜单'}]})
        event = SimpleNamespace(get_messages=lambda: [], message_obj=SimpleNamespace(raw_message=bot),
                                message_str='sub')
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001', 'sub'),
                         ('', self.helpers.QUOTE_BOT))

    def test_quote_shape_reports_fields_without_content(self):
        from types import SimpleNamespace
        raw = SimpleNamespace(raw_data={'content': 'sub', 'message_type': None,
                                        'msg_elements': [{'content': '机密思路', 'message_type': 103}]})
        event = SimpleNamespace(get_messages=lambda: [], message_obj=SimpleNamespace(raw_message=raw))
        shape = self.helpers.quote_shape(event)
        self.assertIn('msg_elements', shape)
        self.assertIn('message_type=None', shape)
        self.assertNotIn('机密思路', shape)
        self.assertEqual(self.helpers.quote_shape(SimpleNamespace()), 'no-raw-data')

    def test_quoted_body_inside_parallel_message(self):
        """真实 QQ 群消息：msg_elements 只给 msg_idx，正文在 parallel_message 里。"""
        from types import SimpleNamespace
        _, _, _, Reply = components()
        def event(parallel):
            raw = SimpleNamespace(raw_data={
                'id': 'm1', 'content': 'sub', 'message_type': 103,
                'message_scene': {'ext': ['qq_official']},
                'author': {'member_openid': 'X'},
                'msg_elements': [{'message_type': 103, 'msg_idx': 0}],
                'parallel_message': parallel,
            })
            return SimpleNamespace(get_messages=lambda: [Reply(id='q1', chain=[], message_str='')],
                                   message_obj=SimpleNamespace(raw_message=raw), message_str='sub')

        for parallel in ([{'content': '暴力', 'message_type': 103, 'id': 'q1'}],
                         {'content': '暴力', 'id': 'q1'}):
            self.assertEqual(self.helpers.collect_quoted_text(event(parallel), 'u1', 'sub'),
                             ('暴力', self.helpers.QUOTE_SELF), parallel)

    def test_parallel_message_without_body_reports_shape_only(self):
        from types import SimpleNamespace
        raw = SimpleNamespace(raw_data={
            'content': 'sub', 'message_type': 103,
            'msg_elements': [{'message_type': 103, 'msg_idx': 0}],
            'parallel_message': [{'message_type': 103, 'id': 'q1'}],
        })
        event = SimpleNamespace(get_messages=lambda: [], message_obj=SimpleNamespace(raw_message=raw),
                                message_str='sub')
        self.assertEqual(self.helpers.collect_quoted_text(event, 'u1', 'sub'),
                         ('', self.helpers.QUOTE_NONE))
        shape = self.helpers.quote_shape(event)
        self.assertIn('msg_idx', shape) or self.assertIn('elements=', shape)

    def test_adapter_dump_markers_are_cleaned(self):
        """AstrBot 摊平引用时塞的 === 消息 1 === / [消息内容] / [关联消息] 要清掉。"""
        _, _, Plain, Reply = components()
        dump = ("=== 消息 1 ===\n[消息内容]  sub 就是 3n\n[消息类型] 引用消息\n"
                "[关联消息]\n--- 第1条 ---\n    [消息内容] 草稿：特判 n=1")
        event = stub.AstrMessageEvent('sub', sender_id='1001',
                                      messages=[Reply(chain=[Plain(dump)], sender_id='1001')])
        text, state = self.helpers.collect_quoted_text(event, '1001')
        self.assertEqual(state, self.helpers.QUOTE_SELF)
        for marker in ('=== 消息', '[消息内容]', '[消息类型]', '第1条 ---'):
            self.assertNotIn(marker, text)
        self.assertIn('就是 3n', text)
        self.assertIn('特判 n=1', text)
        # 正文里不许出现「（他引用的消息）」这类旁白，那是给评委的说明，不是作答。
        for noise in ('（', '）', '引用的引用', '下面是他'):
            self.assertNotIn(noise, text)

    def test_leading_command_word_is_stripped_from_quoted_text(self):
        """被引用的旧消息本身是条指令时，行首的 sub 不该混进作答。"""
        _, _, Plain, Reply = components()
        event = stub.AstrMessageEvent(
            'sub', sender_id='1001',
            messages=[Reply(chain=[Plain('sub 二分答案 + 单调性 check')], sender_id='1001')])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001')[0],
                         '二分答案 + 单调性 check')

    def test_command_word_inside_a_sentence_survives(self):
        _, _, Plain, Reply = components()
        event = stub.AstrMessageEvent(
            'sub', sender_id='1001',
            messages=[Reply(chain=[Plain('答案就是 sub 后面那句的复杂度')], sender_id='1001')])
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001')[0],
                         '答案就是 sub 后面那句的复杂度')

    def test_no_messages_means_no_quote(self):
        event = stub.AstrMessageEvent('sub', sender_id='1001')
        self.assertEqual(self.helpers.collect_quoted_text(event, '1001'),
                         ('', self.helpers.QUOTE_NONE))


class QuotedSubmitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.bot = self.mod.KouHuOJ(type('Context', (), {})(),
                                    {'judge_provider_id': 'text', 'judge_ack_delay_seconds': 0})
        root = Path(self.tmp.name)
        self.bot._store = self.mod.OjStore(root / 'score.db')
        self.bot._cache = self.mod.CfCache(root / 'cache.db')
        self.bot._cf = object()
        self.problem = {'cf_key': '628B', 'contest_id': 628, 'idx': 'B', 'tier': 800,
                        'points': 100, 'statement_en': 'Find max', 'statement_cn': '求最大值'}
        self.bot._cache.save(self.problem)
        self.scope = 'group:100200300'
        self.bot._store.open_problem(self.scope, self.problem, '1001')
        self.bot._judge = AsyncMock(return_value={'verdict': 'AC', 'comment': '思路对'})
        self.bot._pitfalls = type('Pitfalls', (), {
            'get': lambda _self, slug: {'id': slug, 'title': '坑点', 'insight': '视线范围'}})()

    async def asyncTearDown(self):
        self.bot._store.close()
        self.bot._cache.close()
        self.tmp.cleanup()

    def event(self, text, *, quoted_text=None, quoted_sender=None):
        _, _, Plain, Reply = components()
        messages = []
        if quoted_text is not None:
            messages.append(Reply(chain=[Plain(quoted_text)],
                                  sender_id=quoted_sender or '1001'))
        return stub.AstrMessageEvent(text, sender_id='1001', messages=messages)

    async def say(self, event, handler='cmd_submit', submission=''):
        with patch.object(self.mod, 'collect_submission_images', return_value=[]):
            args = (event,) if handler == 'cmd_submit' else (event, submission)
            return '\n'.join([x.text async for x in getattr(self.bot, handler)(*args)])

    async def test_quoting_bot_output_is_refused_with_an_explanation(self):
        out = await self.say(self.event('sub', quoted_text='📚 口胡 OJ · 指令菜单',
                                        quoted_sender='0'))
        self.assertIn('机器人发的消息', out)
        self.bot._judge.assert_not_awaited()

    async def test_quoted_command_word_is_not_judged(self):
        """引用自己刚发的 help 再发 sub：是说指令，不是作答，不该烧一次判题。"""
        out = await self.say(self.event('sub', quoted_text='help'))
        self.assertIn('是指令，不是思路', out)
        self.bot._judge.assert_not_awaited()

    async def test_inline_command_word_is_not_judged(self):
        out = await self.say(self.event('sub new'))
        self.assertIn('是指令，不是思路', out)
        self.bot._judge.assert_not_awaited()

    async def test_short_but_real_analysis_still_judged(self):
        """「暴力」不是指令词，照样要判。"""
        out = await self.say(self.event('sub 暴力'))
        self.assertIn('AC', out)
        self.assertEqual(self.bot._judge.call_args.args[2], '暴力')

    async def test_quoted_own_analysis_is_judged(self):
        out = await self.say(self.event('sub', quoted_text='二分答案然后 check 单调性'))
        self.assertIn('AC', out)
        self.assertEqual(self.bot._judge.call_args.args[2], '二分答案然后 check 单调性')

    async def test_inline_text_and_quote_both_reach_the_judge(self):
        """两条都算：群里常见「先发草稿、再引用它补一句结论」。"""
        await self.say(self.event('sub 我自己的新思路', quoted_text='旧的思路'))
        submitted = self.bot._judge.call_args.args[2]
        self.assertIn('旧的思路', submitted)
        self.assertIn('我自己的新思路', submitted)

    async def test_quoting_others_is_refused_with_an_explanation(self):
        out = await self.say(self.event('sub', quoted_text='别人写的思路', quoted_sender='2002'))
        self.assertIn('引用的是别人发的消息', out)
        self.bot._judge.assert_not_awaited()

    async def test_bare_sub_hint_mentions_quoting(self):
        out = await self.say(self.event('sub'))
        self.assertIn('引用自己那条消息', out)
        self.bot._judge.assert_not_awaited()

    async def test_quoted_text_respects_length_limit(self):
        out = await self.say(self.event('sub', quoted_text='思' * 2001))
        self.assertIn('太长了', out)
        self.bot._judge.assert_not_awaited()

    async def test_grammar_lane_accepts_quoted_analysis(self):
        grammar_scope = self.scope + self.mod.GRAMMAR_SUFFIX
        self.bot._store.open_problem(
            grammar_scope, {'cf_key': self.mod.PITFALL_PREFIX + 'getchar-newline', 'title': '', 'points': 0}, '1001')
        self.bot._judge.return_value = {'verdict': 'AC', 'comment': '抓到点了'}
        out = await self.say(self.event('grm sub', quoted_text='scanf 后面漏了 getchar'),
                             handler='_grammar_submit_once')
        self.assertIn('找到了', out)
        self.assertEqual(self.bot._judge.call_args.args[2], 'scanf 后面漏了 getchar')

    async def test_quote_and_inline_text_are_judged_together(self):
        """引用草稿 + 补一句结论，两句都得算作答，不能只判后面那半句。"""
        out = await self.say(self.event('sub 噢我懂了，前后 ans 上限是 n-2，所以就是 3n',
                                        quoted_text='草稿：特判 n=1；维护 ans 从 1 开始，从 i=2 问到 n'))
        self.assertIn('AC', out)
        submitted = self.bot._judge.call_args.args[2]
        self.assertIn('特判 n=1', submitted)
        self.assertIn('就是 3n', submitted)
        self.assertLess(submitted.index('特判 n=1'), submitted.index('就是 3n'))

    async def test_previous_attempts_ride_along_as_context(self):
        """同一轮里先交过一版，再补一版：旧的当补充资料，不丢。"""
        current = self.bot._store.current(self.scope)
        self.bot._store.note_review(self.scope, current, '1001', '两轮各 2n，一共 4n', 'WA', '超了')
        await self.say(self.event('sub 第二轮复用第一轮的信息，就是 3n'))
        self.assertEqual(self.bot._judge.call_args.kwargs.get('previous'),
                         ['两轮各 2n，一共 4n'])

    async def test_previous_notes_skip_what_the_quote_already_brought(self):
        """引用链里已经摊平了上一版，就别在「本轮此前提交」里再来一遍。"""
        current = self.bot._store.current(self.scope)
        self.bot._store.note_review(self.scope, current, '1001', '上一版：两轮各 2n', 'WA', '')
        await self.say(self.event('sub', quoted_text='上一版：两轮各 2n 加上新结论就是 3n'))
        self.assertEqual(self.bot._judge.call_args.kwargs.get('previous'), [])

    async def test_previous_notes_stay_out_of_other_rounds(self):
        current = self.bot._store.current(self.scope)
        self.bot._store.note_review(self.scope, current, '1001', '本轮旧稿', 'WA', '')
        notes = self.bot._round_notes(self.scope, '1001', '628B')
        self.assertEqual(notes, ['本轮旧稿'])
        self.assertEqual(self.bot._round_notes(self.scope, '1001', '2258C'), [])
        self.assertEqual(self.bot._round_notes(self.scope, '2002', '628B'), [])
        self.assertEqual(self.bot._round_notes(self.scope, '1001', '628B', exclude='本轮旧稿'), [])

    async def test_previous_notes_are_capped(self):
        current = self.bot._store.current(self.scope)
        for text in ('第一版', '第二版', '第三版'):
            self.bot._store.note_review(self.scope, current, '1001', text, 'WA', '')
        notes = self.bot._round_notes(self.scope, '1001', '628B')
        self.assertEqual(len(notes), self.mod.ROUND_NOTES_LIMIT)
        self.assertEqual(notes[0], '第三版')      # 最新的排前面
        self.assertNotIn('第一版', notes)

    async def test_grammar_lane_also_merges_quote_and_text(self):
        grammar_scope = self.scope + self.mod.GRAMMAR_SUFFIX
        self.bot._store.open_problem(
            grammar_scope, {'cf_key': self.mod.PITFALL_PREFIX + 'getchar-newline', 'title': '', 'points': 0}, '1001')
        self.bot._judge.return_value = {'verdict': 'AC', 'comment': '抓到点了'}
        await self.say(self.event('grm sub 就是少读了换行', quoted_text='scanf 后面没 getchar'),
                       handler='_grammar_submit_once', submission='就是少读了换行')
        submitted = self.bot._judge.call_args.args[2]
        self.assertIn('scanf 后面没 getchar', submitted)
        self.assertIn('少读了换行', submitted)

    async def test_judge_prompt_carries_previous_attempts(self):
        problem = {'statement_en': 'Find max', 'examples': [], 'time_limit': 1, 'memory_limit': 256}
        prompt = self.mod.build_judge_prompt(problem, 'sub 就是 3n', previous=['草稿：两轮找最远点'])
        self.assertIn('<previous_attempts>', prompt)
        self.assertIn('两轮找最远点', prompt)
        self.assertIn('只作参考', prompt)
        bare = self.mod.build_judge_prompt(problem, 'sub 就是 3n')
        self.assertNotIn('<previous_attempts>', bare)

    async def test_grammar_lane_hint_mentions_quoting(self):
        grammar_scope = self.scope + self.mod.GRAMMAR_SUFFIX
        self.bot._store.open_problem(
            grammar_scope, {'cf_key': self.mod.PITFALL_PREFIX + 'float-compare', 'title': '', 'points': 0}, '1001')
        out = await self.say(self.event('grm sub'), handler='_grammar_submit_once')
        self.assertIn('引用自己那条消息', out)
        self.bot._judge.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
