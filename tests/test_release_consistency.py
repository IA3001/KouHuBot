"""发版一致性：版本号、更新日志、内嵌副本、群内 `ver` 输出必须互相对得上。

为什么专门写这个文件：0.22.0 发版时踩过一次——metadata.yaml 改成了 v0.22.0、
AstrBot 日志也打印了 v0.22.0，但 main.py 里那份 `PLUGIN_VERSION` 常量还是
0.21.29，于是群里 `ver` 一直报旧版本；而唯一的版本断言又把 0.21.29 写死在
测试里，两边一起过期就成了「假绿」。这里把四处口径串起来，任何一处漏改都会红。
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import _astrbot_stub as stub

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "astrbot_plugin_kouhu_oj"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def metadata_version() -> str:
    text = (PLUGIN_DIR / "metadata.yaml").read_text(encoding="utf-8")
    m = re.search(r"^\s*version:\s*v?(\d+\.\d+\.\d+)", text, re.M)
    return m.group(1) if m else ""


class VersionSourceTests(unittest.TestCase):
    """版本号只能有一个出处，其余全部由它派生。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = stub.load_plugin(Path(cls.tmp.name))
        cls.version = metadata_version()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_metadata_yaml_has_a_version(self):
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")

    def test_plugin_version_follows_metadata(self):
        """main.PLUGIN_VERSION 必须等于 metadata.yaml（群内 `ver` 用的就是它）。"""
        self.assertEqual(self.mod.PLUGIN_VERSION, self.version)

    def test_main_does_not_hardcode_the_version(self):
        """别再写死：常量赋值后面必须是从 metadata.yaml 读出来的调用。"""
        source = (PLUGIN_DIR / "main.py").read_text(encoding="utf-8")
        self.assertIn("PLUGIN_VERSION = _read_plugin_version(", source)
        self.assertNotRegex(source, r'PLUGIN_VERSION\s*=\s*"\d+\.\d+\.\d+"')

    # -- 更新日志 ------------------------------------------------
    def test_changelog_has_current_version_section(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        self.assertIn(f"## {self.version}", text)

    def test_embedded_copy_matches_changelog(self):
        """部署时 .md 可能没同步，内嵌副本是兜底，两者不能走样。"""
        embedded = self.mod._EMBEDDED_CHANGELOG
        self.assertEqual(embedded.strip(), CHANGELOG.read_text(encoding="utf-8").strip())

    def test_changelog_section_and_summary_resolve(self):
        text = self.mod._load_changelog()
        section = self.mod._changelog_section(text, self.version)
        self.assertTrue(section, "切不出当前版本的节，ver 会退化成发整篇日志")
        self.assertIn(self.version, section)
        self.assertTrue(self.mod._changelog_summary(text, self.version))

    def test_newest_kouhu_version_is_the_current_one(self):
        """`ver list` 按出现顺序取版本；新版本必须排在最前，否则列不出来。"""
        text = self.mod._load_changelog()
        versions = re.findall(r"^##\s+(\d+\.\d+\.\d+)(?![\d.])", text, re.M)
        self.assertTrue(versions)
        self.assertEqual(versions[0], self.version, versions[:5])

    def test_heading_with_trailing_title_is_still_parsed(self):
        """允许 `## 0.22.0 · 小标题`：以前这种写法会让 ver 完全看不到这一版。"""
        text = "## 0.99.0 · 带小标题\n\n摘要：示例。\n\n## 0.98.0\n\n摘要：旧版。\n"
        self.assertEqual(
            re.findall(r"^##\s+(\d+\.\d+\.\d+)(?![\d.])", text, re.M), ["0.99.0", "0.98.0"])
        self.assertIn("示例", self.mod._changelog_section(text, "0.99.0"))
        # 版本号前缀不能误吞：0.99.0 不该匹配 0.99.01
        self.assertEqual(self.mod._changelog_section("## 0.99.01\n\nx\n", "0.99.0"), "")


class EmbeddedCopyTests(unittest.TestCase):
    """内嵌副本的生成器：反斜杠不能丢（日志里会写 LaTeX）。"""

    def test_repr_blocks_round_trips_backslashes(self):
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        try:
            import sync_changelog
        finally:
            sys.path.pop(0)
        for text in ('`\\[ x \\]`', '`\\(O(n)\\log n\\)`', '`\\text{数组长度}`',
                     "末尾有反斜杠 \\", "带制表\t符"):
            with self.subTest(text=text):
                scope: dict = {}
                exec("CHANGELOG_MD = " + sync_changelog.repr_blocks(text), scope)
                self.assertEqual(scope["CHANGELOG_MD"], text)

    def test_changelog_mentions_the_current_version_once(self):
        text = CHANGELOG.read_text(encoding="utf-8")
        self.assertEqual(text.count(f"## {metadata_version()}\n"), 1)


class VerCommandTests(unittest.IsolatedAsyncioTestCase):
    """群友实际看到的那两条消息。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = stub.load_plugin(Path(self.tmp.name))
        self.version = metadata_version()
        self.bot = self.mod.KouHuOJ(
            SimpleNamespace(llm_generate=AsyncMock()),
            {'leaderboard_size': 2, 'record_cards_enabled': False},
        )
        await self.bot.initialize()

    async def asyncTearDown(self):
        await self.bot.terminate()
        self.tmp.cleanup()

    async def run_cmd(self, handler, text):
        event = stub.AstrMessageEvent(text, group_id='release', sender_id='alice')
        return [r async for r in getattr(self.bot, handler)(event)]

    def text_of(self, results):
        return "\n".join(getattr(r, 'text', '') or '' for r in results)

    async def test_ver_reports_the_current_version(self):
        out = self.text_of(await self.run_cmd('cmd_version', 'ver'))
        self.assertIn(f"当前版本 {self.version}", out)

    async def test_ver_shows_the_current_summary_not_an_older_release(self):
        out = self.text_of(await self.run_cmd('cmd_version', 'ver'))
        summary = self.mod._changelog_summary(self.mod._load_changelog(), self.version)
        self.assertIn(summary, out)
        # 上一版的摘要不能出现（出现了说明切到了旧的一节）
        older = self.mod._previous_version(self.mod._load_changelog(), self.version) \
            if hasattr(self.mod, '_previous_version') else None
        if older:
            self.assertNotIn(self.mod._changelog_summary(
                self.mod._load_changelog(), older), out)

    async def test_ver_list_starts_with_the_current_version(self):
        out = self.text_of(await self.run_cmd('cmd_version', 'ver list'))
        self.assertIn(f"当前版本 {self.version}", out)
        entries = [line for line in out.splitlines() if re.match(r"^\d+\.\d+\.\d+ · ", line)]
        self.assertTrue(entries, f"列表里没有版本行：{out[:120]!r}")
        self.assertTrue(entries[0].startswith(f"{self.version} · "),
                        f"第一条版本不是当前版本：{entries[0][:60]!r}")
