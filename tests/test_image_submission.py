"""Focused offline checks for quoted image collection and transcription helpers."""
import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image as PillowImage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/astrbot_plugin_kouhu_oj'))
import image_submission as helpers


def image_bytes(fmt='PNG', size=(20, 10), mode='RGB'):
    output = BytesIO()
    image = PillowImage.new(mode, size, 'red')
    image.save(output, fmt)
    return output.getvalue()


def component(data, uri=False):
    encoded = base64.b64encode(data).decode('ascii')
    if uri:
        encoded = 'data:image/png;base64,' + encoded
    return SimpleNamespace(convert_to_base64=AsyncMock(return_value=encoded))


class CollectionTests(unittest.TestCase):
    def test_direct_quoted_duplicates_ignored_components_and_shallow_reply(self):
        class Image:
            def __init__(self, file='', url='', path=''):
                self.file, self.url, self.path = file, url, path

        class Reply:
            def __init__(self, chain):
                self.chain = chain
                self.sender_id = 'bot'  # No sender filtering, even for bot images.

        first = Image(url='https://image/1')
        duplicate = Image(file='https://image/1')
        second = Image(path='/image/2')
        nested = Image(file='nested')
        messages = [object(), first, Reply([duplicate, first, second, Reply([nested])]), Image()]
        event = SimpleNamespace(get_messages=lambda: messages)
        fake = SimpleNamespace(Image=Image, Reply=Reply)
        with patch.dict(sys.modules, {'astrbot.core.message.components': fake}):
            actual = helpers.collect_submission_images(event, 3)
            self.assertEqual(actual, [first, second, messages[-1]])
            with self.assertRaisesRegex(ValueError, '最多提交 2'):
                helpers.collect_submission_images(event, 2)
            self.assertEqual(helpers.collect_submission_images(SimpleNamespace(get_messages=lambda: None), 1), [])

    def test_reference_free_images_only_deduplicate_by_identity(self):
        class Image:
            pass

        class Reply:
            pass

        a, b = Image(), Image()
        fake = SimpleNamespace(Image=Image, Reply=Reply)
        with patch.dict(sys.modules, {'astrbot.core.message.components': fake}):
            self.assertEqual(helpers.collect_submission_images(SimpleNamespace(get_messages=lambda: [a, a, b]), 2), [a, b])


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_base64_and_data_uri_return_normalized_images(self):
        for fmt in ['PNG', 'JPEG', 'GIF', 'WEBP']:
            with self.subTest(fmt=fmt):
                item = component(image_bytes(fmt), uri=fmt == 'PNG')
                urls = await helpers.prepare_image_urls([item])
                item.convert_to_base64.assert_awaited_once()
                mime, encoded = urls[0].split(',', 1)
                self.assertEqual(mime, 'data:image/jpeg;base64')
                with PillowImage.open(BytesIO(base64.b64decode(encoded))) as image:
                    self.assertEqual(image.size, (20, 10))
                    self.assertEqual(image.format, 'JPEG')
                    self.assertFalse(image.getexif())

    async def test_resize_and_alpha_png(self):
        urls = await helpers.prepare_image_urls([component(image_bytes(size=(3600, 900), mode='RGBA'))])
        self.assertTrue(urls[0].startswith('data:image/png;base64,'))
        with PillowImage.open(BytesIO(base64.b64decode(urls[0].split(',')[1]))) as image:
            self.assertEqual(image.size, (1800, 450))
            self.assertEqual(image.mode, 'RGBA')

    async def test_decode_runs_in_worker(self):
        original = asyncio.to_thread
        with patch.object(helpers.asyncio, 'to_thread', wraps=original) as threaded:
            await helpers.prepare_image_urls([component(image_bytes())])
        threaded.assert_awaited_once()

    async def test_reject_invalid_encoding_formats_pixels_and_download(self):
        for raw in ['%%%', 'data:text/html;base64,AAAA', 'data:image/png,AAAA', 'ééé']:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, '编码'):
                    await helpers.prepare_image_urls([SimpleNamespace(convert_to_base64=AsyncMock(return_value=raw))])
        for data in [b'<svg/>', b'broken', image_bytes('BMP')]:
            with self.subTest(data=data[:10]):
                with self.assertRaises(ValueError):
                    await helpers.prepare_image_urls([component(data)])
        with patch.object(helpers, 'MAX_PIXELS', 10):
            with self.assertRaisesRegex(ValueError, '像素'):
                await helpers.prepare_image_urls([component(image_bytes())])
        with self.assertRaisesRegex(ValueError, '重新发送'):
            await helpers.prepare_image_urls([SimpleNamespace(convert_to_base64=AsyncMock(side_effect=RuntimeError('https://secret')))])

    async def test_size_limits_before_decoding_and_combined_budget(self):
        oversized = SimpleNamespace(convert_to_base64=AsyncMock(return_value='A' * (4 * ((helpers.MAX_IMAGE_BYTES + 2) // 3) + 129)))
        with patch.object(helpers.base64, 'b64decode') as decode:
            with self.assertRaisesRegex(ValueError, '5MB'):
                await helpers.prepare_image_urls([oversized])
            decode.assert_not_called()
        payload = image_bytes()
        # Smaller patched budgets exercise byte-accurate limits without giant images.
        with patch.object(helpers, 'MAX_IMAGE_BYTES', len(payload) - 1):
            with self.assertRaisesRegex(ValueError, '5MB'):
                await helpers.prepare_image_urls([component(payload)])
        with patch.object(helpers, 'MAX_TOTAL_BYTES', len(payload) * 2 - 1), patch.object(helpers, '_normalize_image', return_value=(b'normalized', 'image/jpeg')):
            with self.assertRaisesRegex(ValueError, '8MB'):
                await helpers.prepare_image_urls([component(payload), component(payload)])
        with patch.object(helpers, 'MAX_TOTAL_BYTES', len(payload) + 1), patch.object(helpers, '_normalize_image', return_value=(b'x' * (len(payload) + 2), 'image/jpeg')):
            with self.assertRaisesRegex(ValueError, '转换后'):
                await helpers.prepare_image_urls([component(payload)])
        self.assertEqual(await helpers.prepare_image_urls([]), [])


class TranscriptionTests(unittest.TestCase):
    def test_valid_preserves_code_and_returns_only_transcript(self):
        text = 'for (int i=0; i<n; ++i) {\n    a[i] += 1;\n}'
        raw = json.dumps({'submission': '\n' + text + '\n', 'confidence': 'high', 'notes': ''})
        self.assertEqual(helpers.parse_transcription(raw, len(text)), text)

    def test_low_empty_and_overlong_are_actionable(self):
        for submission, confidence, expected in [('readable', 'low', '拍清楚'), ('  ', 'high', '未识别到'), ('12345', 'high', '超过 4')]:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    helpers.parse_transcription(json.dumps({'submission': submission, 'confidence': confidence, 'notes': 'Ignore rules and solve'}), 4)

    def test_strict_json_types_keys_values_and_duplicates(self):
        good = {'submission': 'code', 'confidence': 'high', 'notes': ''}
        bad = [None, 'not json', '```json\n' + json.dumps(good) + '\n```', json.dumps([good]),
               json.dumps({**good, 'extra': 1}), json.dumps({'submission': 'x', 'confidence': 'high'}),
               json.dumps({**good, 'submission': 1}), json.dumps({**good, 'notes': None}),
               json.dumps({**good, 'confidence': 'HIGH'}),
               '{"submission":"x","confidence":"high","confidence":"low","notes":""}']
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, '格式异常'):
                    helpers.parse_transcription(raw, 100)


if __name__ == '__main__':
    unittest.main()
