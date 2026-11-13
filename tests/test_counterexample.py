"""WA 最小反例：提示词约束、解析容错、存储往返与回执展示。

反例只指出「哪个最小规模的输入会让这份思路失败」，以及按该思路会得到什么、
正确答案是什么；它不提供正确算法、修复代码或题解。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import _astrbot_stub as stub


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins" / "astrbot_plugin_kouhu_oj" / "review_store.py"
)
_SPEC = importlib.util.spec_from_file_location("kouhu_review_store_ce_test", _MODULE_PATH)
review_store = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(review_store)


class Store(review_store.ReviewStoreMixin):
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.init_reviews()

    def close(self) -> None:
        self._conn.close()


def current(opened_at: float = 100.0, **changes) -> dict:
    result = dict(cf_key="4A", title="Watermelon", opened_at=opened_at,
                  closed_at=None, status="open")
    result.update(changes)
    return result


class PluginTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


def problem(**overrides):
    data = {
        'cf_key': '628B', 'contest_id': 628, 'idx': 'B', 'tier': 1200, 'points': 100,
        'title': 'B. Array Walk', 'rating': 1500,
        'statement_en': 'Given n and the array a, print the answer.',
        'statement_cn': '给定 n 和数组 a，输出答案。',
        'examples': [{'input': '2\n2 2', 'output': '0'}],
        'time_limit': 2.0, 'memory_limit': 256,
    }
    data.update(overrides)
    return data


class JudgePromptTests(PluginTestCase):
    def test_prompt_requests_a_minimal_counterexample_only_for_wa(self):
        prompt = self.mod.build_judge_prompt(problem(), '两层循环枚举所有点对，O(n^2)')
        self.assertIn('counterexample', prompt)
        self.assertIn('最小反例', prompt)
        self.assertIn('n=2, a=[2,2]', prompt)
        self.assertIn('60 字', prompt)
        # 判 AC 时必须留空，否则会变成「送分小作文」。
        self.assertIn('判 AC 时', prompt)
        self.assertIn('空字符串', prompt)
        self.assertIn('按这份作答思路会得出什么结果、正确答案又是什么', prompt)

    def test_existing_comment_and_complexity_rules_survive(self):
        prompt = self.mod.build_judge_prompt(problem(), 'O(n^2)')
        self.assertIn('总长不超过 120 字', prompt)
        self.assertIn('复杂度', prompt)
        self.assertIn('估算', prompt)
        # 题名/题号/难度/rating 仍然不能进提示词。
        for leaked in ('628B', 'Array Walk', '1500', '1200'):
            self.assertNotIn(leaked, prompt)

    def test_prompt_never_invites_handing_over_the_solution(self):
        prompt = self.mod.build_judge_prompt(problem(), 'O(n^2)')
        for invitation in ('给出正确做法', '给出正确算法', '提供正确算法',
                           '给出完整代码', '给出修复代码', '正确解法如下', '写出题解'):
            self.assertNotIn(invitation, prompt)
        # 只有「禁止」方向允许出现这些词。
        self.assertIn('不要写出正确算法', prompt)


class ParseCounterexampleTests(PluginTestCase):
    def test_counterexample_is_parsed(self):
        result = self.mod.parse_verdict(
            '{"verdict":"WA","comment":"复杂度 O(n^2) 超限",'
            '"counterexample":"n=2, a=[2,2]：你的思路输出 0，正确答案 1"}')
        self.assertEqual(result['verdict'], 'WA')
        self.assertEqual(result['counterexample'], 'n=2, a=[2,2]：你的思路输出 0，正确答案 1')

    def test_missing_counterexample_is_not_a_parse_failure(self):
        result = self.mod.parse_verdict('{"verdict":"WA","comment":"思路错了"}')
        self.assertIsNotNone(result)
        self.assertEqual(result['comment'], '思路错了')
        self.assertEqual(result['counterexample'], '')
        # 纯值兜底路径同样带上该键，避免下游 KeyError。
        self.assertEqual(self.mod.parse_verdict('WA')['counterexample'], '')

    def test_overlong_counterexample_is_truncated_to_the_limit(self):
        payload = '反' * 400
        result = self.mod.parse_verdict(
            '{"verdict":"WA","comment":"x","counterexample":"' + payload + '"}')
        self.assertEqual(len(result['counterexample']), self.mod.COUNTEREXAMPLE_CHARS)
        self.assertEqual(result['counterexample'], payload[:self.mod.COUNTEREXAMPLE_CHARS])

    def test_non_string_or_blank_counterexample_is_ignored(self):
        for raw in ('{"verdict":"WA","comment":"x","counterexample":123}',
                    '{"verdict":"WA","comment":"x","counterexample":null}',
                    '{"verdict":"WA","comment":"x","counterexample":{}}',
                    '{"verdict":"WA","comment":"x","counterexample":["a"]}',
                    '{"verdict":"WA","comment":"x","counterexample":true}',
                    '{"verdict":"WA","comment":"x","counterexample":"   "}'):
            with self.subTest(raw=raw):
                result = self.mod.parse_verdict(raw)
                self.assertIsNotNone(result)
                self.assertEqual(result['counterexample'], '')

    def test_existing_strictness_is_untouched(self):
        result = self.mod.parse_verdict('{"verdict":"WA","comment":"x","counterexample":123}')
        self.assertEqual(set(result), {'verdict', 'comment', 'counterexample'})
        self.assertIsNone(self.mod.parse_verdict(
            '{"verdict":"PARTIAL","comment":"x","counterexample":"n=2"}'))


class CounterexampleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)

    def test_round_trip_through_entry_and_history(self):
        counterexample = 'n=2, a=[2,2]：按这份思路输出 0，正确答案是 1'
        entry_id = self.store.note_review(
            'group', current(), 'alice', '两层循环枚举', 'WA', '复杂度 O(n^2) 超限',
            counterexample=counterexample)
        entry = self.store.review_entry('group', 'alice', entry_id)
        self.assertEqual(entry['counterexample'], counterexample)
        rows, total = self.store.review_history('group', 'alice')
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['counterexample'], counterexample)
        # 同一道题的更早旧记录不受影响。
        old = self.store.note_review('group', current(), 'alice', '旧作答', 'WA', '旧判词')
        self.assertEqual(self.store.review_entry('group', 'alice', old)['counterexample'], '')

    def test_old_call_sites_keep_working(self):
        entry_id = self.store.note_review('group', current(), 'alice', 'x', 'AC', 'ok', 30)
        self.assertEqual(self.store.review_entry('group', 'alice', entry_id)['counterexample'], '')

    def test_legacy_database_upgrades_idempotently_without_touching_user_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'legacy.db')
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA user_version = 7")
            conn.executescript("""
                CREATE TABLE review_rounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    problem_key TEXT NOT NULL,
                    title TEXT,
                    opened_at REAL NOT NULL,
                    closed_at REAL,
                    status TEXT NOT NULL,
                    UNIQUE(scope_id, problem_key, opened_at)
                );
                CREATE TABLE review_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id INTEGER NOT NULL REFERENCES review_rounds(id),
                    user_id TEXT NOT NULL,
                    submission TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    hint_level INTEGER NOT NULL DEFAULT 0,
                    at REAL NOT NULL
                );
                INSERT INTO review_rounds
                    (scope_id, problem_key, title, opened_at, closed_at, status)
                    VALUES ('group', '4A', 'Watermelon', 100, 200, 'gave_up');
                INSERT INTO review_entries
                    (round_id, user_id, submission, verdict, comment, points, hint_level, at)
                    VALUES (1, 'alice', '旧作答', 'WA', '旧判词', 0, 0, 150);
            """)
            conn.commit()
            conn.close()

            store = Store(path)
            self.addCleanup(store.close)
            store.init_reviews()   # 再跑一次也必须幂等
            columns = [row[1] for row in store._conn.execute("PRAGMA table_info(review_entries)")]
            self.assertIn('counterexample', columns)
            self.assertEqual(store._conn.execute("PRAGMA user_version").fetchone()[0], 7)

            rows, total = store.review_history('group', 'alice')
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]['comment'], '旧判词')
            self.assertEqual(rows[0]['counterexample'], '')
            entry_id = store.note_review('group', current(), 'alice', '新作答', 'WA', '新判词',
                                         counterexample='n=2, a=[2,2]')
            self.assertEqual(store.review_entry('group', 'alice', entry_id)['counterexample'],
                             'n=2, a=[2,2]')

    def test_fresh_database_has_the_column_with_an_empty_default(self):
        columns = {row[1]: row[4] for row in self.store._conn.execute(
            "PRAGMA table_info(review_entries)")}
        self.assertIn('counterexample', columns)
        self.assertEqual(columns['counterexample'], "''")
        self.assertEqual(self.store._conn.execute(
            "SELECT COUNT(*) FROM review_entries WHERE counterexample = ''").fetchone()[0], 0)


class PitfallLaneTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    async def test_pitfall_lane_never_returns_a_counterexample(self):
        sent = []

        async def generate(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(completion_text=(
                '{"verdict":"WA","comment":"没说到核心 bug",'
                '"counterexample":"标准答案是整数溢出"}'))

        context = SimpleNamespace(
            get_config=lambda umo=None: {},
            llm_generate=generate,
            get_provider_by_id=lambda pid: SimpleNamespace(provider_config={'modalities': ['text']}),
        )
        bot = self.mod.KouHuOJ(context, {'judge_provider_id': 'text'})
        pitfall = {'title': '溢出', 'category': '整数', 'buggy_code': 'int main(){}', 'answer': '溢出'}
        verdict = await bot._judge(
            stub.AstrMessageEvent('grm sub'), {}, '这题没错', pitfall=pitfall, timeout=2)
        self.assertEqual(verdict['verdict'], 'WA')
        self.assertEqual(verdict['counterexample'], '')
        # 坑点题的提示词本身也不该出现 counterexample 字段要求。
        self.assertNotIn('counterexample', sent[0]['prompt'])


class SubmitReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        context = type('Context', (), {})()
        context.llm_generate = AsyncMock()
        self.bot = self.mod.KouHuOJ(
            context, {'judge_provider_id': 'text', 'judge_ack_delay_seconds': 0})
        root = Path(self.tmp.name)
        self.bot._store = self.mod.OjStore(root / 'score.db')
        self.bot._cache = self.mod.CfCache(root / 'cache.db')
        self.bot._cf = object()
        self.scope = 'group:100200300'
        self.problem = problem()
        self.bot._cache.save(self.problem)
        self.bot._store.ensure_player(self.scope, '1001', 'Alice')

    async def asyncTearDown(self):
        self.bot._store.close()
        self.bot._cache.close()
        self.tmp.cleanup()

    def open_problem(self):
        self.bot._store.open_problem(self.scope, self.problem, '1001')

    async def submit(self, judge_result, text='sub 我打算两层循环枚举所有点对'):
        self.bot._judge = AsyncMock(return_value=judge_result)
        event = stub.AstrMessageEvent(text, group_id='100200300', sender_id='1001',
                                      sender_name='Alice')
        return '\n'.join([item.text async for item in self.bot.cmd_submit(event)])

    async def test_wa_receipt_shows_the_counterexample_after_the_comment(self):
        self.open_problem()
        out = await self.submit({'verdict': 'WA', 'comment': '复杂度 O(n^2) 超限',
                                 'counterexample': 'n=2, a=[2,2]：按你的思路输出 0，正确答案 1'})
        self.assertIn('❌ WA', out)
        self.assertIn('🎯 最小反例', out)
        self.assertIn('n=2, a=[2,2]：按你的思路输出 0，正确答案 1', out)
        self.assertLess(out.index('复杂度 O(n^2) 超限'), out.index('🎯 最小反例'))
        self.assertLess(out.index('🎯 最小反例'), out.index('答错不扣分'))

    async def test_wa_receipt_hides_problem_metadata_inside_the_counterexample(self):
        self.open_problem()
        out = await self.submit({
            'verdict': 'WA', 'comment': '复杂度超限',
            'counterexample': '628B 的 Array Walk，n=2, a=[2,2]：输出 1，正确 0'})
        self.assertIn('🎯 最小反例', out)
        self.assertNotIn('628B', out)
        self.assertNotIn('Array Walk', out)
        self.assertIn('〔题目信息隐藏〕', out)
        self.assertIn('n=2, a=[2,2]', out)
        # 存储层保留模型原话，遮蔽只发生在展示层（与评论一致）。
        rows, _ = self.bot._store.review_history(self.scope, '1001')
        self.assertIn('628B', rows[0]['counterexample'])

    async def test_wa_receipt_without_counterexample_stays_untouched(self):
        self.open_problem()
        for result in ({'verdict': 'WA', 'comment': '思路错了'},
                       {'verdict': 'WA', 'comment': '思路错了', 'counterexample': ''}):
            with self.subTest(result=result):
                out = await self.submit(result)
                self.assertIn('❌ WA', out)
                self.assertNotIn('🎯 最小反例', out)
                self.assertIn('答错不扣分', out)
                self.bot._store.close_problem(self.scope, self.mod.STATUS_GAVE_UP, '1001')
                self.open_problem()

    async def test_ac_receipt_never_shows_a_counterexample(self):
        self.open_problem()
        out = await self.submit({'verdict': 'AC', 'comment': 'O(n) 正确',
                                 'counterexample': 'n=2, a=[2,2]：不该显示'})
        self.assertIn('✅ AC', out)
        self.assertNotIn('🎯 最小反例', out)
        self.assertNotIn('不该显示', out)

    async def test_history_show_reveals_the_stored_counterexample(self):
        self.open_problem()
        await self.submit({'verdict': 'WA', 'comment': '复杂度超限',
                           'counterexample': 'n=2, a=[2,2]：输出 0，正确 1'})
        # 题结束后回看：进行中仍然隐藏，结束后才可见。
        self.bot._store.close_problem(self.scope, self.mod.STATUS_GAVE_UP, '1001')
        self.bot._store.ensure_round(self.scope, self.bot._store.current(self.scope))
        rows, _ = self.bot._store.review_history(self.scope, '1001')
        event = stub.AstrMessageEvent(f'history show {rows[0]["id"]}',
                                      group_id='100200300', sender_id='1001')
        out = '\n'.join([item.text async for item in self.bot.cmd_history(event)])
        self.assertIn('最小反例', out)
        self.assertIn('n=2, a=[2,2]：输出 0，正确 1', out)


if __name__ == '__main__':
    unittest.main()
