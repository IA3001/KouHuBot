"""进度提示「正在判题」应在结果出来后撤回（仅 aiocqhttp）。"""
import tempfile
import unittest
from pathlib import Path

import _astrbot_stub as stub


class FakeBot:
    def __init__(self):
        self.sent, self.deleted = [], []

    async def send_group_msg(self, group_id, message):
        self.sent.append((group_id, message))
        return {"message_id": 777}

    async def delete_msg(self, message_id):
        self.deleted.append(message_id)


class AckRecallTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))

    async def test_send_and_recall(self):
        event = stub.AstrMessageEvent("sub x")
        event.bot = FakeBot()
        cls = self.mod.KouHuOJ
        mid = await cls._send_recallable(event, "hi")
        self.assertEqual(mid, 777)
        self.assertEqual(event.bot.sent, [(100200300, "hi")])
        await cls._recall(event, mid)
        self.assertEqual(event.bot.deleted, [777])

    async def test_no_bot_falls_back(self):
        event = stub.AstrMessageEvent("sub x")
        cls = self.mod.KouHuOJ
        self.assertIsNone(await cls._send_recallable(event, "hi"))
        await cls._recall(event, None)


if __name__ == "__main__":
    unittest.main()
