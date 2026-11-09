"""测试用假平台适配器：把消息灌进真实 AstrBot 管线。

注册成 ``kouhu_test`` 类型，AstrBot 会像加载真实平台一样加载它。
消息从 scenario 文件读入，回复由 FakeMessageEvent 收集后写回结果文件。

关键在于：插件加载、事件管线、Context、渲染、SQLite 全部走真实代码路径。
之前几次线上事故（迁移漏表、漏 await、渲染崩溃）都是「假对象和真实 API
不一致」导致的，这个测试正是为了堵住这一类问题。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from astrbot import logger
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform import (
    AstrBotMessage,
    AstrMessageEvent,
    Group,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
)
from astrbot.core.platform.register import register_platform_adapter


def chain_to_record(chain: MessageChain) -> dict:
    """把消息链压成可断言的记录。"""
    texts: list[str] = []
    images: list[str] = []
    for comp in chain.chain:
        kind = getattr(comp, "type", "")
        if kind == "Plain":
            texts.append(comp.text)
        elif kind == "Image":
            images.append(str(getattr(comp, "file", "") or getattr(comp, "url", "") or ""))
    return {"text": "\n".join(texts), "images": images}


class FakeMessageEvent(AstrMessageEvent):
    """只重写 send，其余全部继承真实实现。"""

    def __init__(self, message_str, message_obj, platform_meta, session_id, platform):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._platform = platform

    async def send(self, message: MessageChain) -> None:
        await super().send(message)  # 保留真实的 trace / 指标逻辑
        self._platform.record(message)

    async def send_streaming(self, generator, use_fallback: bool = False):
        async for chain in generator:
            if chain is not None:
                await self.send(chain)


@register_platform_adapter("kouhu_test", "KouHuBot 集成测试用平台")
class FakePlatform(Platform):
    def __init__(self, config: dict, settings: dict, event_queue: asyncio.Queue) -> None:
        super().__init__(config, event_queue)
        self.settings = settings
        self.scenario_path = Path(config["scenario"])
        self.result_path = Path(config["result_path"])
        self.records: list[dict] = []
        self.step = -1

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="kouhu_test",
            description="集成测试用平台",
            id=str(self.config.get("id")),
            support_proactive_message=False,
            support_streaming_message=False,
        )

    def create_event(self, message: AstrBotMessage) -> FakeMessageEvent:
        return FakeMessageEvent(
            message.message_str,
            message,
            self.meta(),
            message.session_id,
            self,
        )

    def run(self):
        return self._run()

    async def _run(self) -> None:
        steps = json.loads(self.scenario_path.read_text(encoding="utf-8"))
        logger.info(f"[kouhu-test] 场景共 {len(steps)} 步")
        for index, step in enumerate(steps):
            self.step = index
            before = len(self.records)
            self._inject(step)
            await self._wait_quiet(
                timeout=float(step.get("timeout", 90)),
                settle=float(step.get("settle", 2.0)),
                min_replies=int(step.get("expect_replies", 1)),
                before=before,
            )
            logger.info(
                f"[kouhu-test] 第 {index} 步 {step.get('text', '')!r} "
                f"-> {len(self.records) - before} 条回复"
            )
            self._flush(index)
        self._flush(-1, done=True)
        # 让事件总线把剩余任务跑完再退出
        await asyncio.sleep(1)

    def _inject(self, step: dict) -> None:
        group_id = str(step.get("group", "g1"))
        abm = AstrBotMessage()
        abm.self_id = "kouhu_test"
        abm.sender = MessageMember(
            user_id=str(step.get("user", "u1")),
            nickname=step.get("name", "测试"),
        )
        abm.type = MessageType.GROUP_MESSAGE
        abm.group = Group(group_id=group_id)
        abm.session_id = group_id
        abm.message_id = uuid.uuid4().hex
        abm.message = [Plain(step["text"])]
        abm.message_str = step["text"]
        abm.timestamp = int(time.time())
        abm.raw_message = None
        self.commit_event(self.create_event(abm))

    async def _wait_quiet(
        self, timeout: float, settle: float, min_replies: int, before: int
    ) -> None:
        """等到「至少收到 min_replies 条回复」且随后静默 settle 秒，或超时。

        必须要求最小条数：判题要调模型，几秒内没有任何回复很常见，
        只按静默判断会把这一步当成「没有回复」，然后回复落到下一步去。
        """
        deadline = time.monotonic() + timeout
        last = len(self.records)
        quiet_since = time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            if len(self.records) != last:
                last = len(self.records)
                quiet_since = time.monotonic()
            elif (
                len(self.records) - before >= min_replies
                and time.monotonic() - quiet_since >= settle
            ):
                return

    def _flush(self, step: int, done: bool = False) -> None:
        payload = {"step": step, "done": done, "records": self.records}
        tmp = self.result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.result_path)

    def record(self, message_chain: MessageChain) -> None:
        """记一条回复，并打上当前步骤号。"""
        item = chain_to_record(message_chain)
        item["step"] = self.step
        self.records.append(item)

    async def send_by_session(self, session, message_chain: MessageChain) -> None:
        self.record(message_chain)
