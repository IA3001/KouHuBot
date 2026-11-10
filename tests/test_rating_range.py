import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_kouhu_oj'))
import types
import logging
from unittest.mock import patch
with patch.dict(sys.modules, {'astrbot':types.ModuleType('astrbot'), 'astrbot.api':types.SimpleNamespace(logger=logging.getLogger('range-test'))}):
    from sources.codeforces import CodeforcesSource

class RatingRangeTest(unittest.TestCase):
    def setUp(self):
        self.source = object.__new__(CodeforcesSource)
        self.source._catalog = [dict(cf_key=str(i), rating=r, tier=1200) for i,r in enumerate([1200,1300,1400,1500,1600])]
        self.source._catalog.append(dict(cf_key='unrated',rating=None,tier=1200))

    def test_exact_bounds_and_exclusions(self):
        self.assertEqual(self.source.pick_range(1300,1300)['rating'],1300)
        self.assertEqual(self.source.pick_range(1200,1600,{'0','1','2','3'})['rating'],1600)
        self.assertEqual(self.source.pick_range(1200,1600,{'1','2','3','4'})['rating'],1200)
        self.assertIsNone(self.source.pick_range(1350,1399))
        self.assertIsNone(self.source.pick_range(1200,1600,{'0','1','2','3','4'}))

    def test_actual_rating_not_tier(self):
        for _ in range(30):
            self.assertIn(self.source.pick_range(1350,1550)['rating'],[1400,1500])

    def test_unique_candidates_use_uniform_choice(self):
        self.source._catalog.append(dict(self.source._catalog[0]))
        for method,args,expected in ((self.source.pick_range,(1200,1600),5),(self.source.pick,(1200,),6)):
            with patch('secrets.choice',side_effect=lambda pool:pool[-1]) as choice:
                method(*args)
                pool = choice.call_args.args[0]
                self.assertEqual(len(pool),expected)
                self.assertEqual(len({p['cf_key'] for p in pool}),expected)

    def test_translation_failure_never_returns_english_as_chinese(self):
        import asyncio
        from unittest.mock import Mock, AsyncMock
        source=self.source
        source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',contest_id=1,idx='A',statement_en='Find the maximum possible sum of all the numbers in the array.',statement_cn=None,examples='[]')
        source._translate=AsyncMock(side_effect=RuntimeError('provider unavailable'))
        with self.assertRaisesRegex(RuntimeError,'中文题面翻译未完成'):
            asyncio.run(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',title='test',rating=800,tier=800)))
        source._cache.bump_used.assert_not_called()

    def test_complete_cache_avoids_scraping(self):
        import asyncio
        from unittest.mock import Mock
        source = self.source
        cached = dict(cf_key='1',contest_id=1,idx='A',rating=1300,tier=1200,
                      title='test',statement_en='Find $$$x$$$.',statement_cn='求 $$$x$$$。',examples='[]')
        source._cache = Mock()
        source._cache.get.return_value = cached
        source._scrape_sync = Mock(side_effect=AssertionError('cache hit must not scrape'))
        problem = asyncio.run(source.fetch(cached))
        self.assertEqual(problem['statement_cn'],'求 $$$x$$$。')
        source._scrape_sync.assert_not_called()

if __name__=='__main__': unittest.main()
