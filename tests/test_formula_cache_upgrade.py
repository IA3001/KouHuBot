"""Old statement caches re-fetch semantic math; current caches stay offline."""
import asyncio
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import _astrbot_stub as stub


class FormulaCacheTest(unittest.TestCase):
    def test_old_statement_is_refetched_and_retranslated_once(self):
        with tempfile.TemporaryDirectory() as d:
            m = stub.load_plugin(Path(d))
            cache = m.CfCache(Path(d)/'cf.db')
            old = {'cf_key':'507B','contest_id':507,'idx':'B','rating':1400,'tier':1400,'points':320,
                   'statement_en':'r <= 105','statement_cn':'半径不超过105','statement_parser_version':1,'sample_parser_version':2}
            cache.save(old)
            source = m.CodeforcesSource(cache=cache, translate=AsyncMock(return_value='半径 ⟦0⟧'))
            parsed = {'statement_en':'radius $$$10^{5}$$$', 'title':'Amr','examples':[{'input':'1','output':'2'}], 'time_limit':1,'memory_limit':256,'image_urls':[]}
            with patch.object(source, '_scrape_sync', return_value=parsed) as scrape, patch('sources.codeforces.download_images', return_value=[]):
                problem = asyncio.run(source.fetch(old))
                self.assertIn('10^{5}',problem['statement_cn'])
                self.assertEqual(cache.get('507B')['statement_parser_version'],2)
                again=asyncio.run(source.fetch(old))
                self.assertEqual(again['statement_cn'],problem['statement_cn'])
                self.assertEqual(scrape.call_count,1)
            cache.close()

    def test_failed_source_repair_preserves_old_cache_for_retry(self):
        with tempfile.TemporaryDirectory() as d:
            m=stub.load_plugin(Path(d));cache=m.CfCache(Path(d)/'cf.db')
            old={'cf_key':'507B','contest_id':507,'idx':'B','statement_en':'105','statement_cn':'105','statement_parser_version':1}
            cache.save(old);before=cache.get('507B');source=m.CodeforcesSource(cache=cache)
            with patch.object(source,'_scrape_sync',side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):asyncio.run(source.fetch(old))
            self.assertEqual(cache.get('507B'),before)
            cache.close()
