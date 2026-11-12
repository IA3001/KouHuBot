"""Review output stays complete and navigation preserves both pagination modes."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import _astrbot_stub as stub


class ReviewUXTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.bot = self.mod.KouHuOJ(type('Context', (), {})(), {'leaderboard_size': 2})
        await self.bot.initialize()
        self.event = stub.AstrMessageEvent('history', group_id='review', sender_id='alice')
        self.scope = self.bot._scope_of(self.event)

    async def asyncTearDown(self):
        await self.bot.terminate()
        self.tmp.cleanup()

    async def drive(self, handler, text):
        event = stub.AstrMessageEvent(text, group_id='review', sender_id='alice')
        return [result.text async for result in getattr(self.bot, handler)(event)]

    def add_review(self, submission, comment, key='628B', scope=None):
        scope = scope or self.scope
        current = dict(cf_key=key, title='Example', opened_at=100, status='ac')
        return self.bot._store.note_review(scope, current, 'alice', submission, 'AC', comment, 10)

    async def test_long_submission_and_comment_can_be_reconstructed(self):
        submission = '0123456789' * 570 + '作答结束'
        comment = '判词内容' * 700 + '判词结束'
        rid = self.add_review(submission, comment)
        first = '\n'.join(await self.drive('cmd_history', 'history show 628B'))
        self.assertIn(f'history show {rid}', first)
        pages = []
        for page in range(1, 10):
            out = '\n'.join(await self.drive('cmd_history', f'history show {rid} {page}'))
            if '没有这一内容页' in out:
                break
            self.assertIn(f'记录 #{rid}', out)
            pages.append(out.split('【内容开始】\n', 1)[1].split('\n【内容结束】', 1)[0])
        combined = ''.join(pages)
        self.assertIn(submission, combined)
        self.assertIn(comment, combined)
        self.assertGreater(len(pages), 2)

    async def test_problem_entry_pages_stay_compatible_and_grm_works(self):
        for n in range(4):
            self.add_review(f'answer {n}', 'ok')
        out = '\n'.join(await self.drive('cmd_history', 'history show 628B 2'))
        self.assertIn('2/2 页', out)
        self.assertIn('answer 0', out)
        grm_scope = self.scope + self.mod.GRAMMAR_SUFFIX
        rid = self.add_review('语法分析' * 700, 'ok', self.mod.PITFALL_PREFIX + 'test', grm_scope)
        out = '\n'.join(await self.drive('cmd_history', f'history grm show {rid} 2'))
        self.assertIn(f'history grm show {rid}', out)
        self.assertIn('内容页 2/', out)

    async def test_content_page_validation_and_record_privacy(self):
        rid = self.add_review('private answer', 'ok')
        for token in ('0', '-1', 'abc', '9999999'):
            out = '\n'.join(await self.drive('cmd_history', f'history show {rid} {token}'))
            self.assertIn('提交编号 [内容页]', out)
            self.assertNotIn('private answer', out)
        event = stub.AstrMessageEvent(f'history show {rid} 1', group_id='review', sender_id='bob')
        out = '\n'.join([result.text async for result in self.bot.cmd_history(event)])
        self.assertIn('找不到', out)
        self.assertNotIn('private answer', out)

    async def test_open_problem_blocks_content_pages(self):
        rid = self.add_review('secret', 'secret')
        self.bot._store.open_problem(self.scope, {'cf_key': '628B', 'title': '', 'points': 0}, 'alice')
        out = '\n'.join(await self.drive('cmd_history', f'history show {rid} 2'))
        self.assertIn('仍在进行', out)
        self.assertNotIn('secret', out)

    async def test_giveup_delivers_entire_answer_and_fix_in_bounded_chunks(self):
        answer = '解释内容' * 450
        fix = 'int main() { /* correct code */ }\n' * 180
        scope = self.scope + self.mod.GRAMMAR_SUFFIX
        self.bot._store.open_problem(scope, {'cf_key': self.mod.PITFALL_PREFIX + 'long', 'title': 'Long', 'points': 0}, 'alice')
        with patch.object(self.bot._pitfalls, 'get', return_value={'title': 'Long', 'answer': answer, 'fix': fix}):
            outputs = await self.drive('_grammar_giveup', 'grm giveup')
        text = ''.join(outputs)
        self.assertIn(answer, text)
        self.assertIn(fix, text)
        self.assertGreater(len(outputs), 2)
        self.assertTrue(all(len(item) <= 1800 for item in outputs))

    async def test_rank_paginates_full_board_with_absolute_tied_ranks(self):
        for n, score in enumerate((100, 90, 90, 80, 70)):
            uid = f'u{n}'
            self.bot._store.ensure_player(self.scope, uid, f'Player{n}')
            self.bot._store._conn.execute(
                'UPDATE players SET total_score=?, solved=1 WHERE scope_id=? AND user_id=?',
                (score, self.scope, uid))
        self.bot._store._conn.commit()
        self.assertTrue(self.mod.RE_RANK.match('rank 2'))
        self.assertFalse(self.mod.RE_RANK.match('ranking'))
        with patch.object(self.mod, 'render_leaderboard', return_value=None):
            first = '\n'.join(await self.drive('cmd_rank', 'rank'))
            second = '\n'.join(await self.drive('cmd_rank', 'rank 2'))
            last = '\n'.join(await self.drive('cmd_rank', 'rank 3'))
            invalid = '\n'.join(await self.drive('cmd_rank', 'rank 0'))
        self.assertIn('共 5 人', first)
        self.assertIn('rank 2', first)
        self.assertIn('2. Player2', second)
        self.assertIn('4. Player3', second)
        self.assertIn('5. Player4', last)
        self.assertIn('rank [页码]', invalid)

    async def test_image_rank_also_has_navigation(self):
        for n in range(3):
            self.bot._store.ensure_player(self.scope, str(n), str(n))
        self.bot._store._conn.execute('UPDATE players SET solved=1 WHERE scope_id=?', (self.scope,))
        self.bot._store._conn.commit()
        with patch.object(self.mod, 'render_leaderboard', return_value='rank.png'):
            out = '\n'.join(await self.drive('cmd_rank', 'rank'))
        self.assertIn('rank 2', out)
        self.assertIn('共 3 人', out)

    def test_independent_lane_rule_is_explicit(self):
        text = self.mod.rules_text()
        self.assertIn('无需等另一线路结束', text)
        self.assertNotIn('两条线都需答对或放弃', text)


if __name__ == '__main__':
    unittest.main()
