import asyncio
import base64
from io import BytesIO
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
with patch.dict(sys.modules, {'astrbot': types.ModuleType('astrbot'), 'astrbot.api': types.SimpleNamespace(logger=logging.getLogger('image-test'))}):
    from sources.codeforces import CfCache, CodeforcesSource, _text_of, parse_statement, _DDL, protect_tex, restore_tex
    from sources import statement_images as images


def png_bytes():
    from PIL import Image
    output = BytesIO()
    Image.new('RGB', (3, 2), 'red').save(output, 'PNG')
    return output.getvalue()


def response(data=b'', status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.iter_content.return_value = [data]
    return result


class StatementImagesTest(unittest.TestCase):
    def test_modern_sample_lines_and_literal_output_are_preserved(self):
        parsed=parse_statement('<div class="problem-statement"><div class="sample-tests"><div class="input"><div class="title">Input</div><pre><div class="test-example-line">2</div><div class="test-example-line">  1 2</div></pre></div><div class="output"><div class="title">Output</div><pre>Output is correct<br/>YES</pre></div></div></div>')
        self.assertEqual(parsed['examples'],[{'input':'2\n  1 2','output':'Output is correct\nYES'}])

    def test_display_and_inline_formulas_restore_exact_delimiters(self):
        text=r'计算 $$$x$$$ 与 $$$$$$\sum_i x_i$$$$$$。'
        protected,formulas=protect_tex(text)
        self.assertEqual(len(formulas),2)
        self.assertEqual(restore_tex(protected,formulas),text)

    def test_pre_code_div_lines_are_preserved(self):
        self.assertIn('first\nsecond',_text_of('<pre><div>first</div><div>second</div></pre>'))

    def test_text_images_keep_positions_and_attributes(self):
        urls = []
        text = _text_of('Before<img alt="a > b" src="/userpic/a.png">middle<img src=\'https://codeforces.org/userpic/b.png?x=1&amp;y=2\'>after', urls)
        # The ordinary img tag parser supports quoted/unquoted attributes.
        self.assertEqual(text, 'BeforeKOUHUIMAGE0ENDmiddleKOUHUIMAGE1ENDafter')
        self.assertEqual(urls, ['https://codeforces.com/userpic/a.png', 'https://codeforces.org/userpic/b.png?x=1&y=2'])

    def test_statement_includes_note_images_in_order(self):
        parsed = parse_statement('''<div class="problem-statement"><div class="header"><div class="title">A. Images</div></div><div><p>Body<img src="/userpic/a.png">end.</p></div><div class="input-specification"><div class="section-title">Input</div>Read<img src="/userpic/b.png"></div><div class="note"><div class="section-title">Note</div>Note<img src="https://codeforces.org/userpic/c.png"></div></div>''')
        self.assertEqual(len(parsed['image_urls']), 3)
        self.assertIn('BodyKOUHUIMAGE0ENDend.', parsed['statement_en'])
        self.assertIn('ReadKOUHUIMAGE1END', parsed['statement_en'])
        self.assertIn('NoteKOUHUIMAGE2END', parsed['statement_en'])

    def test_reject_untrusted_urls_without_network(self):
        bad = ['http://codeforces.com/a', 'https://evil.test/a', 'https://codeforces.com.evil.test/a', 'https://user@codeforces.com/a', 'https://codeforces.com:443/a', 'file:///a', 'data:image/png,a', 'https://codeforces.com\\@evil.test/a', 'https://codeforces.com/a\n']
        result = images.download_images(bad)
        self.assertTrue(all(not x['src'] and 'error' in x for x in result))
        for host in images.ALLOWED_HOSTS:
            self.assertEqual(images.validate_url(f'https://{host}/userpic/a'), f'https://{host}/userpic/a')

    def test_download_reencodes_png_and_blocks_redirect_escape(self):
        session = Mock()
        good = response(png_bytes())
        redirect = response(status=302, headers={'Location': 'https://evil.test/image'})
        session.get.side_effect = [good, redirect]
        with patch.dict(sys.modules, {'curl_cffi': types.SimpleNamespace(requests=types.SimpleNamespace(Session=Mock(return_value=session)))}):
            result = images.download_images(['https://codeforces.org/userpic/a.png', 'https://codeforces.com/a'])
        self.assertTrue(result[0]['src'].startswith('data:image/png;base64,'))
        from PIL import Image
        with Image.open(BytesIO(base64.b64decode(result[0]['src'].split(',')[1]))) as image:
            self.assertEqual(image.size, (3, 2))
            self.assertEqual(image.format, 'PNG')
        self.assertIn('error', result[1])
        self.assertEqual(session.get.call_count, 2)
        self.assertTrue(all(c.kwargs['allow_redirects'] is False and c.kwargs['stream'] for c in session.get.call_args_list))
        good.close.assert_called_once()
        redirect.close.assert_called_once()
        session.close.assert_called_once()

    def test_limits_format_pixels_and_count(self):
        from PIL import Image
        for payload in [b'<svg onload="alert(1)"></svg>', b'not an image']:
            with self.assertRaisesRegex(ValueError, 'SVG'):
                images._png(payload)
        with patch.object(images, 'MAX_PIXELS', 5):
            with self.assertRaisesRegex(ValueError, '像素'):
                images._png(png_bytes())
        session = Mock()
        session.get.return_value = response(png_bytes(), headers={'Content-Length': str(images.MAX_IMAGE_BYTES + 1)})
        with patch.dict(sys.modules, {'curl_cffi': types.SimpleNamespace(requests=types.SimpleNamespace(Session=Mock(return_value=session)))}):
            result = images.download_images(['https://codeforces.com/a'] * 13)
        self.assertEqual(len(result), 13)
        self.assertEqual(session.get.call_count, 12)
        self.assertTrue(all('error' in item for item in result))
        self.assertIn('12 张', result[-1]['error'])

    def test_stream_and_batch_limits_and_bounded_redirects(self):
        for overrides, payloads in [
            ({'MAX_IMAGE_BYTES': 5}, [b'123', b'456']),
            ({'MAX_TOTAL_BYTES': 5}, [b'123', b'456']),
        ]:
            session = Mock()
            streamed = response()
            streamed.iter_content.return_value = payloads
            session.get.return_value = streamed
            with patch.dict(sys.modules, {'curl_cffi': types.SimpleNamespace(requests=types.SimpleNamespace(Session=Mock(return_value=session)))}), patch.multiple(images, **overrides):
                result = images.download_images(['https://codeforces.com/a'])
            self.assertIn('大小限制', result[0]['error'])
            streamed.close.assert_called_once()
        session = Mock()
        session.get.return_value = response(status=302, headers={'Location': '/userpic/again'})
        with patch.dict(sys.modules, {'curl_cffi': types.SimpleNamespace(requests=types.SimpleNamespace(Session=Mock(return_value=session)))}):
            result = images.download_images(['https://codeforces.com/a'])
        self.assertEqual(session.get.call_count, images.MAX_REDIRECTS + 1)
        self.assertIn('重定向次数', result[0]['error'])

    def test_all_supported_formats_reencode_and_drop_metadata(self):
        from PIL import Image, PngImagePlugin
        for fmt in ('PNG', 'JPEG', 'GIF', 'WEBP'):
            output = BytesIO()
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('unsafe', '<script>payload</script>')
            Image.new('RGB', (4, 3)).save(output, fmt, **({'pnginfo': metadata} if fmt == 'PNG' else {}))
            clean = images._png(output.getvalue())
            with Image.open(BytesIO(clean)) as decoded:
                self.assertEqual(decoded.format, 'PNG')
                self.assertNotIn('unsafe', decoded.info)

    def test_cache_migration_preserves_existing_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cf.db'
            connection = sqlite3.connect(path)
            connection.execute(_DDL.replace('        images_json   TEXT,\n', ''))
            connection.execute("INSERT INTO cf_problems (cf_key,title,used_count) VALUES ('1A','Existing',4)")
            connection.commit()
            connection.close()
            cache = CfCache(path)
            self.assertEqual(cache.get('1A')['used_count'], 4)
            self.assertIsNone(cache.get('1A')['images_json'])
            record = dict(cf_key='1A', title='Existing', statement_en='KOUHUIMAGE0END', statement_cn='KOUHUIMAGE0END', statement_images=[dict(token='KOUHUIMAGE0END', src='data:image/png;base64,abc', url='https://codeforces.com/a')])
            cache.save(record)
            self.assertEqual(CodeforcesSource._row_to_problem(cache.get('1A'))['statement_images'], record['statement_images'])
            cache.save(dict(record, statement_en='NewKOUHUIMAGE0END', statement_cn=None))
            self.assertIsNone(cache.get('1A')['statement_cn'])
            self.assertEqual(cache.get('1A')['used_count'], 4)
            cache.close()

    def test_translation_never_receives_markers(self):
        source = CodeforcesSource(Mock(), translate=AsyncMock(return_value='译文'))
        source._cache.get.return_value = dict(cf_key='1A', statement_en='BeforeKOUHUIMAGE0ENDAfter', statement_cn='坏缓存', images_json=json.dumps([dict(token='KOUHUIMAGE0END', src='data:image/png;base64,abc')]))
        problem = asyncio.run(source.fetch(dict(cf_key='1A', contest_id=1, idx='A', rating=800)))
        self.assertEqual(problem['statement_cn'], '译文KOUHUIMAGE0END译文')
        self.assertEqual([call.args[0] for call in source._translate.await_args_list], ['Before', 'After'])

    def test_old_placeholder_refetches_and_failure_is_visible(self):
        source = CodeforcesSource(Mock(),translate=AsyncMock(side_effect=['前面','后面']))
        source._cache.get.return_value = dict(cf_key='1A', statement_en='Old〔图〕', statement_cn='旧〔图〕')
        source._scrape_sync = Mock(return_value=dict(title='new', statement_en='BeforeKOUHUIMAGE0ENDAfter', image_urls=['https://evil.test/a'], examples=[], time_limit=2, memory_limit=256))
        problem = asyncio.run(source.fetch(dict(cf_key='1A', contest_id=1, idx='A', rating=800)))
        source._scrape_sync.assert_called_once()
        self.assertIn('前面〔图片加载失败', problem['statement_cn'])
        self.assertTrue(problem['statement_cn'].endswith('〕后面'))
        self.assertEqual(problem['statement_images'][0]['src'], '')


if __name__ == '__main__':
    unittest.main()
