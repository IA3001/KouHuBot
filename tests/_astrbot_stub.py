"""
AstrBot 桩模块（仅用于本地测试）

在没安装 AstrBot 的机器上，把 `astrbot.*` 需要的部分替换成最小可用实现，
这样就可以直接加载 KouHuBot 插件并驱动它，不需要 Docker、不需要 QQ 凭证。

注意：这是「测试替身」，不是 AstrBot 的完整模拟。
它的用途是验证插件自身的逻辑与 AstrBot API 的调用方式是否吻合。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_MAIN = REPO_ROOT / "plugins" / "astrbot_plugin_kouhu_oj" / "main.py"


class _Logger:
    """把 AstrBot 的 logger 静音，避免测试输出被淹没。"""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose

    def _emit(self, level: str, *args, **kwargs) -> None:
        if self.verbose:
            message = " ".join(str(a) for a in args)
            print(f"[{level}] {message}")

    def info(self, *args, **kwargs) -> None:
        self._emit("INFO", *args, **kwargs)

    def warning(self, *args, **kwargs) -> None:
        self._emit("WARN", *args, **kwargs)

    def error(self, *args, **kwargs) -> None:
        self._emit("ERROR", *args, **kwargs)

    def debug(self, *args, **kwargs) -> None:
        self._emit("DEBUG", *args, **kwargs)


class FakeMessageEventResult:
    """模拟 event.plain_result() 的返回值。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return f"PlainResult({self.text!r})"


class AstrMessageEvent:
    """模拟 AstrBot 的消息事件，只实现插件实际用到的那部分接口。"""

    def __init__(
        self,
        message: str,
        *,
        group_id: str = "100200300",
        sender_id: str = "1001",
        sender_name: str = "群友",
        umo: str | None = None,
        role: str = "member",
        messages: list | None = None,
    ) -> None:
        self.message_str = message
        self._group_id = group_id
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.role = role
        self._umo = umo or f"qq_official:GroupMessage:{group_id}"
        self._stopped = False
        self._messages = list(messages or [])

    # ---- 插件实际调用的接口 ----
    def get_messages(self) -> list:
        """消息段列表；引用（Reply）与图片都挂在这里。"""
        return self._messages
    def stop_event(self) -> None:
        """吃掉这条消息，后续 handler 不再执行。

        真实接口在 astrbot.core.platform.astr_message_event.AstrMessageEvent 上，
        是本插件挡掉 AstrBot 内置 /help、/new 的关键。
        """
        self._stopped = True

    def is_stopped(self) -> bool:
        return self._stopped

    def get_group_id(self) -> str:
        return self._group_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def is_admin(self) -> bool:
        # 真实 AstrBot 里就是判 role == "admin"
        return self.role == "admin"

    @property
    def unified_msg_origin(self) -> str:
        return self._umo

    def plain_result(self, text: str) -> FakeMessageEventResult:
        return FakeMessageEventResult(text)

    def image_result(self, url_or_path: str) -> FakeMessageEventResult:  # 未被使用，占位
        return FakeMessageEventResult(f"[image] {url_or_path}")


def install_stubs(*, data_root: Path | None = None, verbose: bool = False) -> None:
    """注册 astrbot.* 桩模块。

    Args:
        data_root: 模拟 AstrBot 数据根目录；插件会把状态写在
                   <data_root>/plugin_data/astrbot_plugin_kouhu_oj/ 下。
        verbose: 是否打印插件内部日志。
    """

    def new_module(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        sys.modules[name] = module
        return module

    # ---- astrbot / astrbot.api ----
    astrbot_pkg = new_module("astrbot")

    api = new_module("astrbot.api")
    api.logger = _Logger(verbose)
    astrbot_pkg.api = api

    # ---- astrbot.api.event ----
    event_mod = new_module("astrbot.api.event")
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageEventResult = FakeMessageEventResult
    event_mod.filter = _FilterProxy()

    # ---- astrbot.api.star ----
    class Context:
        """桩 Context，插件只通过它访问插件数据目录之外的少量能力。"""

    star_mod = new_module("astrbot.api.star")
    star_mod.Context = Context
    star_mod.Star = _StarBase

    # ---- astrbot.core.utils.astrbot_path.get_astrbot_data_path ----
    core = new_module("astrbot.core")
    astrbot_pkg.core = core
    utils = new_module("astrbot.core.utils")
    core.utils = utils
    astrbot_path = new_module("astrbot.core.utils.astrbot_path")
    utils.astrbot_path = astrbot_path

    resolved_root = Path(data_root) if data_root is not None else Path.cwd() / "data"

    def get_astrbot_data_path() -> str:
        resolved_root.mkdir(parents=True, exist_ok=True)
        return str(resolved_root)

    astrbot_path.get_astrbot_data_path = get_astrbot_data_path

    # ---- astrbot.core.message.components ----
    # 引用的消息、图片都靠这几个段类型识别，桩里必须能 isinstance 判断。
    message_mod = new_module("astrbot.core.message")
    core.message = message_mod
    components = new_module("astrbot.core.message.components")
    message_mod.components = components

    class BaseMessageComponent:
        """消息段基类：只存属性。"""

        def __init__(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    class Plain(BaseMessageComponent):
        def __init__(self, text: str = "") -> None:
            self.text = text

    class Image(BaseMessageComponent):
        def __init__(self, file: str = "", url: str = "", path: str = "") -> None:
            self.file, self.url, self.path = file, url, path

    class At(BaseMessageComponent):
        def __init__(self, qq: str = "") -> None:
            self.qq = qq

    class Reply(BaseMessageComponent):
        def __init__(self, id="", chain=None, sender_id=0, sender_nickname="", time=0, message_str=""):
            self.id = id
            self.chain = list(chain or [])
            self.sender_id = sender_id
            self.sender_nickname = sender_nickname
            self.time = time
            self.message_str = message_str

    components.BaseMessageComponent = BaseMessageComponent
    components.Plain = Plain
    components.Image = Image
    components.At = At
    components.Reply = Reply


class _FilterProxy:
    """@filter.regex / @filter.command 的桩：原样返回被装饰函数。"""

    @staticmethod
    def regex(pattern, **kwargs):
        def decorator(func):
            func._kouhu_regex = pattern
            return func

        return decorator

    @staticmethod
    def command(*args, **kwargs):
        def decorator(func):
            return func

        return decorator


class _StarBase:
    """桩 Star 基类：只保存 context 与 config。"""

    def __init__(self, context, config=None) -> None:
        self.context = context
        self.config = config or {}


def load_plugin(data_root: Path | None = None, *, verbose: bool = False):
    """安装桩并加载插件模块，返回模块对象。"""
    install_stubs(data_root=data_root, verbose=verbose)
    spec = importlib.util.spec_from_file_location("kouhu_oj_plugin", PLUGIN_MAIN)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载插件: {PLUGIN_MAIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
