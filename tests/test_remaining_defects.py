"""Regression tests for concurrent delivery, round identity and category cycling."""
import asyncio
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import _astrbot_stub as stub


class DefectTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.m=stub.load_plugin(Path(self.tmp.name))
        self.bot=self.m.KouHuOJ(SimpleNamespace(),{'new_cooldown_seconds':0,'grm_auto_growth':False})
        self.bot._store=self.m.OjStore(Path(self.tmp.name)/'score.db')
        self.bot._cache=self.m.CfCache(Path(self.tmp.name)/'cache.db')
        self.bot._pitfalls=self.m.PitfallSource();self.bot._image_dir=Path(self.tmp.name)/'images';self.bot._image_dir.mkdir()
        self.a={'cf_key':'4A','contest_id':4,'idx':'A','rating':800,'tier':800,'points':100,'statement_en':'Max','statement_cn':'最大值'}
        self.b={**self.a,'cf_key':'5A','contest_id':5}
        class Source:
            _catalog=[self.a,self.b]
            async def refresh_catalog(self):pass
            async def fetch(inner,entry):
                self.bot._cache.save(entry);return dict(entry)
        self.bot._cf=Source();self.bot._render_cf=lambda p:[]
        self.event=stub.AstrMessageEvent('new CF4A');self.scope='group:100200300'
    async def asyncTearDown(self):
        await self.bot.terminate();self.tmp.cleanup()
    async def drain(self,gen):return [item.text async for item in gen]

    async def test_two_new_calls_cannot_overwrite_on_notice_yield(self):
        first=self.bot.cmd_new(self.event)
        self.assertIn('正在选题', (await anext(first)).text)
        second=await self.drain(self.bot.cmd_new(stub.AstrMessageEvent('new CF5A')))
        self.assertIn('正在准备', ''.join(second))
        await self.drain(first)
        self.assertEqual(self.bot._store.current(self.scope)['cf_key'],'4A')
        self.assertEqual(self.bot._store.state(self.scope)['tier_issued'],1)

    async def test_done_pending_cannot_be_claimed_during_foreground_yield(self):
        first=self.bot.cmd_new(self.event);await anext(first)
        second=await self.drain(self.bot.cmd_problem(stub.AstrMessageEvent('cur')))
        await self.drain(first)
        self.assertEqual(self.bot._store.state(self.scope)['tier_issued'],1)

    async def test_hint_does_not_apply_to_same_key_new_round(self):
        self.bot._store.open_problem(self.scope,self.a,'1001');self.bot._cache.save(self.a)
        async def provider(event):return 'mock'
        self.bot._provider_id=provider
        started=asyncio.Event();release=asyncio.Event()
        async def generate(**kwargs):
            started.set();await release.wait();return SimpleNamespace(completion_text='["方向","观察","步骤"]')
        self.bot.context.llm_generate=generate
        task=asyncio.create_task(self.drain(self.bot.cmd_hint(stub.AstrMessageEvent('hint 3'))))
        await started.wait();self.bot._store.close_problem(self.scope,'gave_up','1001');self.bot._store.open_problem(self.scope,self.a,'1001');release.set()
        output=await task
        self.assertIn('本次提示不再展示',''.join(output))
        self.assertEqual(self.bot._store._conn.execute('SELECT COUNT(*) FROM review_hint_uses').fetchone()[0],0)

    async def test_exhausted_alias_category_resets_only_that_category(self):
        scope=self.scope+self.m.GRAMMAR_SUFFIX
        for item in self.bot._pitfalls.all_items():self.bot._store.note_pitfall(scope,item['id'])
        unrelated=next(x['id'] for x in self.bot._pitfalls.all_items() if x['category']!='输入输出')
        with patch.object(self.m,'render_problem',return_value=None):
            out=await self.drain(self.bot._grammar_new(stub.AstrMessageEvent('grm new io'),'io'))
        self.assertIn('重新开始',''.join(out))
        self.assertIn(unrelated,self.bot._store.used_pitfalls(scope))
        self.assertEqual(self.bot._store.current(scope)['status'],'open')

    async def test_image_cleanup_keeps_recent_files(self):
        old=self.bot._image_dir/'old.png';recent=self.bot._image_dir/'recent.png'
        old.write_bytes(b'old');recent.write_bytes(b'new');os.utime(old,(time.time()-90000,)*2)
        self.bot._cleanup_images();self.assertFalse(old.exists());self.assertTrue(recent.exists())
        self.bot._image_cleanup_task=asyncio.create_task(self.bot._image_cleanup_loop())
        await asyncio.sleep(0)
        task=self.bot._image_cleanup_task
        await self.bot.terminate();self.assertTrue(task.cancelled())
