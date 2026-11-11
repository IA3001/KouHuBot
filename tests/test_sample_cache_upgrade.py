"""Sample cache upgrades use local SQLite and mocked source pages only."""
import asyncio
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_kouhu_oj'))
with patch.dict(sys.modules, {
    'astrbot': types.ModuleType('astrbot'),
    'astrbot.api': types.SimpleNamespace(logger=logging.getLogger('sample-cache-test')),
}):
    from sources import codeforces as cf


ENTRY = dict(cf_key='123A', contest_id=123, idx='A', rating=800)
OLD = [{'input': '21 2', 'output': '3YES'}]
PAGE = '''<div class="problem-statement"><div><p>Replacement prose.</p></div>
<div class="sample-tests"><div class="input"><div class="title">Input</div>
<pre><div class="test-example-line">2</div><div class="test-example-line">  1 2</div></pre></div>
<div class="output"><div class="title">Output</div><pre>3<br/>YES</pre></div></div></div>'''
FIXED = [{'input': '2\n  1 2', 'output': '3\nYES'}]


class SampleCacheUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cache.db'
        self.cache = cf.CfCache(self.path)
        self.addCleanup(self.cache.close)
        self.translate = AsyncMock(side_effect=AssertionError('repair must not translate'))
        self.source = cf.CodeforcesSource(self.cache, translate=self.translate)
        self.source._scrape_sync = Mock(return_value=cf.parse_statement(PAGE))
        self.images = patch.object(cf, 'download_images', side_effect=AssertionError('repair must not download images')).start()
        self.addCleanup(patch.stopall)

    def seed(self, **overrides):
        record = dict(ENTRY, tier=800, points=10, title='Keep title',
                      statement_en='Keep $$$x$$$ KOUHUIMAGE0END.',
                      statement_cn='保留 $$$x$$$ KOUHUIMAGE0END。',
                      statement_images=[dict(token='KOUHUIMAGE0END', src='data:image/png;base64,abc')],
                      examples=OLD, time_limit=1.5, memory_limit=128,
                      fetched_at=100.0, translated_at=101.0)
        record.update(overrides)
        self.cache.save(record)
        self.cache.bump_used(ENTRY['cf_key'])
        return self.cache.get(ENTRY['cf_key'])

    def test_additive_migration_preserves_all_legacy_values(self):
        legacy_path = Path(self.temp.name) / 'legacy.db'
        with sqlite3.connect(legacy_path) as conn:
            conn.execute(cf._DDL.replace('        sample_parser_version INTEGER DEFAULT 1,\n', ''))
            conn.execute('INSERT INTO cf_problems (cf_key, statement_cn, examples_json, images_json, used_count) VALUES (?, ?, ?, ?, ?)',
                         ('123A', '译文', json.dumps(OLD), '[{"src":"saved"}]', 9))
        legacy = cf.CfCache(legacy_path)
        try:
            row = legacy.get('123A')
            self.assertEqual(row['sample_parser_version'], 1)
            self.assertEqual(row['statement_cn'], '译文')
            self.assertEqual(row['examples_json'], json.dumps(OLD))
            self.assertEqual(row['images_json'], '[{"src":"saved"}]')
            self.assertEqual(row['used_count'], 9)
        finally:
            legacy.close()
        reopened = cf.CfCache(legacy_path)
        reopened.close()

    def test_fetch_repairs_only_samples_once_and_preserves_other_columns(self):
        before = self.seed()
        result = asyncio.run(self.source.fetch(ENTRY))
        self.assertEqual(result['examples'], FIXED)
        after = self.cache.get('123A')
        self.assertEqual(after['sample_parser_version'], 2)
        for key, value in before.items():
            if key not in ('examples_json', 'sample_parser_version'):
                self.assertEqual(after[key], value, key)
        self.assertEqual(json.loads(after['examples_json']), FIXED)
        self.assertEqual(asyncio.run(self.source.fetch(ENTRY))['examples'], FIXED)
        self.source._scrape_sync.assert_called_once_with('123A', 123, 'A')
        self.translate.assert_not_awaited()
        self.images.assert_not_called()

    def test_explicit_repair_returns_renderable_problem_without_fetch(self):
        self.seed(contest_id=None, idx=None)
        result = asyncio.run(self.source.repair_samples('123A'))
        self.assertEqual(result['examples'], FIXED)
        self.assertEqual(result['statement_cn'], '保留 $$$x$$$ KOUHUIMAGE0END。')
        self.source._scrape_sync.assert_called_once_with('123A', 123, 'A')
        self.assertEqual(self.cache.get('123A')['used_count'], 1)
        self.translate.assert_not_awaited()
        self.images.assert_not_called()

    def test_failures_keep_entire_cache_and_remain_retryable(self):
        before = self.seed()
        for failure in (RuntimeError('offline'), {}, {'statement_en': 'page', 'examples': []},
                        {'statement_en': 'page', 'examples': [{'input': 'partial'}]}):
            with self.subTest(failure=failure):
                self.source._scrape_sync.side_effect = failure if isinstance(failure, Exception) else None
                self.source._scrape_sync.return_value = failure
                result = asyncio.run(self.source.fetch(ENTRY))
                self.assertEqual(result['examples'], OLD)
                self.assertEqual(self.cache.get('123A'), before)
        self.assertEqual(self.source._scrape_sync.call_count, 4)
        self.translate.assert_not_awaited()
        self.images.assert_not_called()

    def test_failed_repair_then_translation_save_keeps_legacy_samples(self):
        self.seed(statement_en='Find the answer.', statement_cn=None, statement_images=[])
        self.source._scrape_sync.side_effect = RuntimeError('offline')
        self.source._translate = AsyncMock(return_value='求答案。')
        result = asyncio.run(self.source.fetch(ENTRY))
        self.assertEqual(result['examples'], OLD)
        row = self.cache.get('123A')
        self.assertEqual(json.loads(row['examples_json']), OLD)
        self.assertEqual(row['sample_parser_version'], 1)
        self.assertEqual(row['statement_cn'], '求答案。')

    def test_explicit_repair_failure_returns_old_problem_and_unknown_is_none(self):
        before = self.seed()
        self.source._scrape_sync.side_effect = RuntimeError('offline')
        self.assertEqual(asyncio.run(self.source.repair_samples('123A'))['examples'], OLD)
        self.assertEqual(self.cache.get('123A'), before)
        self.assertIsNone(asyncio.run(self.source.repair_samples('404A')))

    def test_fresh_fetch_persists_version_two_and_uses_cache_next_time(self):
        self.images.side_effect = None
        self.images.return_value = []
        self.source._translate = AsyncMock(return_value='新译文。')
        result = asyncio.run(self.source.fetch(ENTRY))
        self.assertEqual(result['examples'], FIXED)
        self.assertEqual(self.cache.get('123A')['sample_parser_version'], 2)
        asyncio.run(self.source.fetch(ENTRY))
        self.source._scrape_sync.assert_called_once()
        self.source._translate.assert_awaited_once()
        self.images.assert_called_once_with([])

    def test_markerless_complete_mock_cache_does_not_scrape(self):
        row = self.seed()
        del row['sample_parser_version']
        self.source._cache = Mock()
        self.source._cache.get.return_value = row
        result = asyncio.run(self.source.fetch(ENTRY))
        self.assertEqual(result['examples'], OLD)
        self.source._scrape_sync.assert_not_called()
        self.source._cache.save.assert_not_called()
        self.source._cache.save_samples.assert_not_called()
        self.translate.assert_not_awaited()

    def test_current_complete_cache_never_scrapes(self):
        self.seed(sample_parser_version=2)
        asyncio.run(self.source.fetch(ENTRY))
        asyncio.run(self.source.repair_samples('123A'))
        self.source._scrape_sync.assert_not_called()
        self.translate.assert_not_awaited()

    def test_ordinary_save_keeps_upgraded_version(self):
        self.seed(sample_parser_version=2)
        self.cache.save(dict(ENTRY, statement_cn='保留译文', examples=FIXED))
        self.assertEqual(self.cache.get('123A')['sample_parser_version'], 2)

    def test_cancelled_repair_propagates_without_marking_success(self):
        before = self.seed()
        with patch.object(cf.asyncio, 'to_thread', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(self.source.repair_samples('123A'))
        self.assertEqual(self.cache.get('123A'), before)


if __name__ == '__main__':
    unittest.main()
