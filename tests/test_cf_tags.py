import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('cf_tag_parser',Path(__file__).resolve().parents[1]/'plugins/astrbot_plugin_kouhu_oj/cf_tags.py')
tags=importlib.util.module_from_spec(spec);spec.loader.exec_module(tags)

class TagParserTest(unittest.TestCase):
    available=['dp','trees','graphs','binary search','data structures','2-sat','meet-in-the-middle','future new tag']
    def test_every_live_tag_is_supported(self):
        for tag in self.available:
            self.assertEqual(tags.parse_tag_query(tag,self.available),([tag],None))
    def test_alias_multi_tag_and_ranges(self):
        self.assertEqual(tags.parse_tag_query('动态规划+树 2',self.available),(['dp','trees'],(1400,2100)))
        self.assertEqual(tags.parse_tag_query('BINARY SEARCH [1200-1600]',self.available),(['binary search'],(1200,1600)))
        self.assertEqual(tags.parse_tag_query('dp,dp',self.available),(['dp'],None))
    def test_unknown_or_bad_range_fails(self):
        for query in ('unknown','dp 1600-1200','dp+','dp 500-1000'):
            with self.assertRaises(ValueError):tags.parse_tag_query(query,self.available)
