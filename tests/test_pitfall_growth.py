import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/astrbot_plugin_kouhu_oj'))
import types
import logging
from unittest.mock import patch
with patch.dict(sys.modules,{'astrbot':types.ModuleType('astrbot'),'astrbot.api':types.SimpleNamespace(logger=logging.getLogger('growth'))}):
    from sources.pitfall_growth import GrowthBank,trusted_url,MaterialParser
    from sources.pitfalls import PitfallSource
from growth_probe import CANDIDATE

class GrowthTest(unittest.TestCase):
    def test_cpp_standard_aliases(self):
        for spelling in ('C++17','GNU++17','cpp17','-std=c++17','17'):
            with tempfile.TemporaryDirectory() as directory:
                bank=GrowthBank(directory)
                item=bank.stage(dict(CANDIDATE,cpp_standard=spelling),{'url':'https://oi-wiki.org/lang/csl/','hash':'x'},[])
                self.assertEqual(item['cpp_standard'],'c++17')

    def test_sources_are_restricted(self):
        self.assertTrue(trusted_url('https://oi-wiki.org/lang/csl/'))
        for url in ('http://oi-wiki.org/','https://evil.org/a','https://oi-wiki.org.evil.org/a','https://x@oi-wiki.org/a','https://oi-wiki.org:444/a'):
            self.assertFalse(trusted_url(url))
    def test_pending_isolation_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            bank=GrowthBank(directory)
            material={'url':'https://oi-wiki.org/lang/csl/','hash':'a'*64}
            item=bank.stage(CANDIDATE,material,[])
            source=PitfallSource(growth_file=bank.path)
            self.assertIsNone(source.get(item['id']))
            bank.finish(item,{'ok':False,'reason':'sandbox absent','evidence':{}})
            self.assertIsNone(PitfallSource(growth_file=bank.path).get(item['id']))
            bank.finish(item,{'ok':True,'reason':'counterexample','evidence':{}})
            loaded=PitfallSource(growth_file=bank.path).get(item['id'])
            self.assertTrue(loaded['verified'])
            self.assertEqual(loaded['source'],material['url'])
            self.assertEqual(len(GrowthBank(directory).state['approved']),1)
            with self.assertRaises(ValueError): bank.stage(CANDIDATE,material,[])
    def test_parser_does_not_include_script(self):
        p=MaterialParser();p.feed('<h1>C++20</h1><script>bad()</script><p>vector</p><a href="/cpp/test">next</a>')
        self.assertNotIn('bad()', ''.join(p.parts))
        self.assertIn('/cpp/test',p.links)

if __name__=='__main__':unittest.main()
