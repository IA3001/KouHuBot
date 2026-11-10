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

if __name__=='__main__': unittest.main()
