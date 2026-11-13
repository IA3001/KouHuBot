"""ask 快照里的「当前开放题」上下文。

题号/题名/rating 在题目结束前不能进快照（群里看到题号就能去搜题解），
但题面早就发在群里了。这些测试锁住这条边界：
放题面、放样例、放提问者自己写过的作答，但不放标识信息，也不放坑点题的答案。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _astrbot_stub as stub  # noqa: E402


class FakeStore:
    def __init__(self, current, rows=None):
        self._current = current
        self._rows = rows or []

    def current(self, scope):
        return self._current

    def review_history(self, scope, user_id, page=1, page_size=5):
        return list(self._rows), len(self._rows)


class FakeCache:
    def __init__(self, problems):
        self._problems = problems

    def get(self, cf_key):
        return self._problems.get(cf_key)


class OpenProblemContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = stub.load_plugin(Path(tempfile.mkdtemp()))

    def build(self, current, problems, rows=None):
        plugin = SimpleNamespace(
            _store=FakeStore(current, rows),
            _cache=FakeCache(problems),
            config={},
        )
        return plugin

    def context(self, current, problems, rows=None, user_id='u1'):
        plugin = self.build(current, problems, rows)
        return self.mod.KouHuOJ._open_problem_context(plugin, 'group', user_id)

    def problem(self, **over):
        data = {
            'cf_key': '628B',
            'title': 'B. Chat Online',
            'rating': 1600,
            'statement_cn': '给定一棵树，求直径长度。',
            'examples': [{'input': '3\n1 2\n2 3', 'output': '2'}],
        }
        data.update(over)
        return data

    def test_open_problem_brings_statement_and_examples(self):
        context = self.context({'cf_key': '628B', 'status': 'open'}, {'628B': self.problem()})
        self.assertIn('直径', context['题面'])
        self.assertIn('样例 1', context['样例'])
        self.assertIn('题号', context['说明'])

    def test_open_problem_hides_identifying_fields(self):
        context = self.context({'cf_key': '628B', 'status': 'open'}, {'628B': self.problem()})
        blob = json.dumps(context, ensure_ascii=False)
        self.assertNotIn('628B', blob)
        self.assertNotIn('Chat Online', blob)
        self.assertNotIn('1600', blob)   # rating 数值也不许出现

    def test_statement_mentions_of_title_are_scrubbed(self):
        problem = self.problem(statement_cn='这题来自 Codeforces Round 628B，求树的直径。')
        context = self.context({'cf_key': '628B', 'status': 'open'}, {'628B': problem})
        self.assertNotIn('628B', context['题面'])
        self.assertIn('〔题目信息隐藏〕', context['题面'])

    def test_statement_is_clipped(self):
        problem = self.problem(statement_cn='树' * 5000)
        context = self.context({'cf_key': '628B', 'status': 'open'}, {'628B': problem})
        self.assertLessEqual(len(context['题面']), self.mod.ASK_STATEMENT_CHARS + 20)
        self.assertIn('已截断', context['题面'])

    def test_image_tokens_never_reach_the_snapshot(self):
        problem = self.problem(statement_cn='题面 KOUHUIMAGE3END 继续')
        context = self.context({'cf_key': '628B', 'status': 'open'}, {'628B': problem})
        self.assertNotIn('KOUHUIMAGE', context['题面'])

    def test_asker_own_submission_rides_along(self):
        rows = [
            {'cf_key': '628B', 'verdict': 'WA', 'submission': '两次 dfs 求直径',
             'comment': '没考虑单点情况'},
            {'cf_key': 'others', 'verdict': 'AC', 'submission': '无关', 'comment': ''},
        ]
        context = self.context({'cf_key': '628B', 'status': 'open'}, {'628B': self.problem()}, rows)
        latest = context['提问者在这道题上的最近一次提交']
        self.assertEqual(latest['作答'], '两次 dfs 求直径')
        self.assertEqual(latest['判定'], 'WA')
        self.assertEqual(latest['判词'], '没考虑单点情况')

    def test_other_peoples_rows_do_not_leak_in(self):
        rows = [{'cf_key': 'other-problem', 'verdict': 'AC', 'submission': '别人这题', 'comment': ''}]
        context = self.context({'cf_key': '628B', 'status': 'open'}, {'628B': self.problem()}, rows)
        self.assertNotIn('提问者在这道题上的最近一次提交', context)

    def test_closed_problem_returns_none(self):
        self.assertIsNone(self.context({'cf_key': '628B', 'status': 'solved'}, {'628B': self.problem()}))

    def test_pitfall_problem_never_exposes_content(self):
        context = self.context({'cf_key': 'grm:int-overflow', 'status': 'open'}, {})
        self.assertIn('坑点题', context['说明'])
        self.assertNotIn('题面', context)

    def test_uncached_problem_says_so_instead_of_guessing(self):
        context = self.context({'cf_key': '9999Z', 'status': 'open'}, {})
        self.assertIn('缓存', context['说明'])
        self.assertNotIn('题面', context)

    def test_ask_prompt_says_hidden_metadata_is_intentional(self):
        prompt = self.mod.ASK_SYSTEM_PROMPT
        self.assertIn('刻意隐藏', prompt)
        self.assertIn('不要猜题号', prompt)

    def test_ask_prompt_points_this_at_the_current_problem(self):
        prompt = self.mod.ASK_SYSTEM_PROMPT
        self.assertIn('本群当前题目', prompt)
        self.assertIn('不要直接写出完整算法', prompt)


if __name__ == '__main__':
    unittest.main()
