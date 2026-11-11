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

    def test_tag_intersection_range_and_unique_sampling(self):
        self.source._catalog=[dict(cf_key='1A',rating=1200,tags=['dp','trees']),dict(cf_key='1A',rating=1200,tags=['dp','trees']),dict(cf_key='2A',rating=1800,tags=['dp']),dict(cf_key='3A',rating=1000,tags=['graphs'])]
        self.assertEqual(self.source.tag_counts(),{'dp':2,'graphs':1,'trees':1})
        self.assertEqual(self.source.pick_tags(['dp','trees'])['cf_key'],'1A')
        self.assertIsNone(self.source.pick_tags(['dp','trees'],1400,2000))
        with patch('secrets.choice',side_effect=lambda pool:pool[0]) as choice:
            self.source.pick_tags(['dp'])
            self.assertEqual(len(choice.call_args.args[0]),2)

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

    def test_cancellation_stops_translation_without_retry_or_opening(self):
        import asyncio
        from unittest.mock import Mock, AsyncMock
        source=self.source;source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',contest_id=1,idx='A',statement_en='Find the answer.',statement_cn=None,examples='[]')
        cancelled=[]
        async def slow(text):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        source._translate=AsyncMock(side_effect=slow)
        async def run():
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',rating=800)),0.01)
        asyncio.run(run())
        self.assertEqual(cancelled,[True])
        self.assertEqual(source._translate.await_count,1)
        source._cache.bump_used.assert_not_called()

    def test_multiple_paragraphs_share_one_cloud_request(self):
        import asyncio
        from unittest.mock import Mock, AsyncMock
        source=self.source;source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',contest_id=1,idx='A',statement_en='Find the sum.\n\nInput\nThe array is given.\n\nOutput\nPrint the answer.',statement_cn=None,examples='[]')
        source._translate=AsyncMock(return_value='求和。\n\n输入\n给定数组。\n\n输出\n打印答案。')
        asyncio.run(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',rating=800)))
        self.assertEqual(source._translate.await_count,1)

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

    def test_dropped_formula_recovers_by_translating_only_prose(self):
        import asyncio
        from unittest.mock import Mock, AsyncMock
        source=self.source
        source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',contest_id=1,idx='A',statement_en='Find the maximum value of $$$x$$$ in the array.',statement_cn=None,examples='[]')
        source._translate=AsyncMock(side_effect=['求数组最大值。','{"0":"求最大值","2":"在数组中。"}'])
        result=asyncio.run(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',title='test',rating=800,tier=800)))
        self.assertIn('$$$x$$$',result['statement_cn'])
        self.assertIn('求最大值',result['statement_cn'])
        self.assertEqual(source._translate.await_count,2)
        self.assertTrue(all('⟦' not in call.args[0] for call in source._translate.await_args_list[1:]))

    def test_fragment_english_echo_retries_before_failing(self):
        import asyncio
        from unittest.mock import Mock, AsyncMock
        source=self.source; source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',contest_id=1,idx='A',statement_en='Find the maximum value of $$$x$$$ in the array.',statement_cn=None,examples='[]')
        source._translate=AsyncMock(side_effect=['求最大值。','{"0":"Find the maximum value of","2":"在数组中。"}','{"0":"求最大值"}'])
        result=asyncio.run(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',title='test',rating=800,tier=800)))
        self.assertIn('求最大值$$$x$$$',result['statement_cn'])
        self.assertEqual(source._translate.await_count,3)
        self.assertNotIn('"2"',source._translate.await_args_list[-1].args[0])

    def test_catalog_excludes_special_and_output_only_tasks(self):
        from unittest.mock import Mock,patch
        payload={'status':'OK','result':{'problems':[
            dict(contestId=1002,index='D2',rating=1400,tags=['*special'],type='PROGRAMMING'),
            dict(contestId=1,index='A',rating=800,tags=[],type='PROGRAMMING'),
            dict(contestId=2,index='A',rating=1200,tags=[],type='QUESTION')]}}
        self.source._timeout=15
        response=Mock();response.json.return_value=payload
        with patch('curl_cffi.requests.get',return_value=response):
            entries=self.source._fetch_catalog_sync()
        self.assertEqual([e['cf_key'] for e in entries],['1A'])

    def test_transient_translation_timeout_retries_once(self):
        import asyncio
        from unittest.mock import Mock,AsyncMock
        source=self.source;source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',statement_en='Find the sum.',statement_cn=None)
        source._translate=AsyncMock(side_effect=[TimeoutError(), '求和。'])
        result=asyncio.run(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',rating=800)))
        self.assertEqual(result['statement_cn'],'求和。')
        self.assertEqual(source._translate.await_count,2)

    def test_repeated_translation_timeout_stops_after_one_retry(self):
        import asyncio
        from unittest.mock import Mock,AsyncMock
        source=self.source;source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',statement_en='Find the sum.',statement_cn=None)
        source._translate=AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(RuntimeError):
            asyncio.run(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',rating=800)))
        self.assertEqual(source._translate.await_count,2)
        source._cache.save.assert_not_called()

    def test_short_english_echo_uses_batch_recovery(self):
        import asyncio
        from unittest.mock import Mock, AsyncMock
        source=self.source;source._cache=Mock()
        source._cache.get.return_value=dict(cf_key='1A',contest_id=1,idx='A',statement_en='Find the sum.',statement_cn='Find the sum.',examples='[]')
        source._translate=AsyncMock(side_effect=['Find the sum.','{"0":"求和。"}'])
        result=asyncio.run(source.fetch(dict(cf_key='1A',contest_id=1,idx='A',rating=800)))
        self.assertEqual(result['statement_cn'],'求和。')
        self.assertEqual(source._translate.await_count,2)

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
