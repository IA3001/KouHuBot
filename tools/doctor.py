#!/usr/bin/env python3
"""接入前自检：把「能不能跑起来」拆成一条条可验证的检查。

在服务器上跑：
    ./run.sh doctor
或者直接：
    python3 tools/doctor.py

只依赖标准库，不需要虚拟环境。
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / ".local" / "astrbot" / "data" / "cmd_config.json"
PLUGIN = REPO / "plugins" / "astrbot_plugin_kouhu_oj"
PITFALLS = PLUGIN / "pitfalls.json"

PASS, FAIL, WARN = "✅", "❌", "⚠️ "
results: list[tuple[str, str, str]] = []


def check(level: str, title: str, detail: str = "") -> None:
    results.append((level, title, detail))
    print(f"{level} {title}" + (f"\n     {detail}" if detail else ""))


def check_config() -> dict | None:
    if not CONFIG.is_file():
        check(FAIL, "AstrBot 配置文件不存在", f"{CONFIG}\n     先跑 ./run.sh 让它初始化一次")
        return None
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        check(FAIL, "配置文件不是合法 JSON", str(exc))
        return None
    check(PASS, "AstrBot 配置文件可读")
    return cfg


def check_platform(cfg: dict) -> dict | None:
    platforms = [p for p in cfg.get("platform", []) if p.get("type") == "qq_official"]
    if not platforms:
        check(FAIL, "没有配置 QQ 官方机器人平台", "WebUI → 平台 → 新增 qq_official")
        return None
    plat = platforms[0]
    if not plat.get("enable"):
        check(FAIL, "QQ 官方机器人平台未启用", "WebUI 里把开关打开")
        return None
    if not plat.get("appid") or not plat.get("secret"):
        check(FAIL, "AppID 或 Secret 为空", "在 QQ 开放平台后台获取后填入")
        return None
    check(PASS, "QQ 官方机器人平台已启用", f"AppID {plat['appid']}")
    if plat.get("use_markdown"):
        check(WARN, "use_markdown 是开着的", "群聊不支持 markdown，建议关掉（本仓库默认已关）")
    else:
        check(PASS, "use_markdown 已关闭（群聊更稳）")
    return plat


def check_credentials(plat: dict) -> None:
    """用腾讯的 token 接口验证 AppID/Secret 是否有效。"""
    print("  正在向腾讯验证凭证 ...")
    req = urllib.request.Request(
        "https://bots.qq.com/app/getAppAccessToken",
        data=json.dumps(
            {"appId": str(plat["appid"]), "clientSecret": str(plat["secret"])}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        check(FAIL, "凭证验证请求被拒绝", f"HTTP {exc.code}: {exc.read().decode()[:200]}")
        return
    except Exception as exc:  # noqa: BLE001
        check(WARN, "连不上腾讯接口，无法验证凭证", f"{type(exc).__name__}: {exc}")
        return
    if "access_token" in body:
        check(PASS, "AppID / Secret 有效", f"access_token 有效期 {body.get('expires_in')} 秒")
    else:
        check(FAIL, "AppID / Secret 无效", json.dumps(body, ensure_ascii=False)[:300])


def check_provider(cfg: dict) -> None:
    providers = [p for p in cfg.get("provider", []) if p.get("enable")]
    if not providers:
        check(FAIL, "没有启用任何模型提供商", "WebUI → 模型提供商里加一个")
        return
    check(PASS, "已启用模型提供商", f"{providers[0].get('id')} / {providers[0].get('model')}")


def check_plugin() -> None:
    if not (PLUGIN / "main.py").is_file():
        check(FAIL, "插件目录不存在", str(PLUGIN))
        return
    if not PITFALLS.is_file():
        check(FAIL, "坑点库 pitfalls.json 缺失", str(PITFALLS))
    else:
        try:
            data = json.loads(PITFALLS.read_text(encoding="utf-8"))
            check(PASS, "坑点库可读", f"{len(data)} 条")
        except ValueError as exc:
            check(FAIL, "坑点库不是合法 JSON", str(exc))

    installed = REPO / ".local" / "astrbot" / "data" / "plugins" / PLUGIN.name
    if installed.is_dir():
        check(PASS, "插件已同步到 AstrBot", str(installed))
    else:
        check(WARN, "插件还没同步到 AstrBot", "跑一次 ./run.sh 会自动同步")

    plugin_set = json.loads(CONFIG.read_text(encoding="utf-8-sig")).get("plugin_set", [])
    if plugin_set == ["*"] or PLUGIN.name in str(plugin_set):
        check(PASS, "插件处于启用状态")
    else:
        check(WARN, "插件可能没被启用", f"plugin_set = {plugin_set}")


def check_fonts() -> None:
    """没中文字体的话，题目图片会退化成纯文本。"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    found = [p for p in candidates if Path(p).exists()]
    if any("CJK" in p or "wqy" in p or "PingFang" in p for p in found):
        check(PASS, "找到中文字体", found[0])
    elif found:
        check(WARN, "只有西文字体，中文会显示成方块", "apt install -y fonts-noto-cjk")
    else:
        check(WARN, "没找到任何字体", "apt install -y fonts-noto-cjk")


def check_port() -> None:
    try:
        with socket.create_connection(("127.0.0.1", 6185), timeout=1):
            check(WARN, "6185 端口被占用", "AstrBot 已经在跑，或者有残留进程")
    except OSError:
        check(PASS, "6185 端口空闲")


def main() -> int:
    print("=" * 62)
    print("KouHuBot 接入自检")
    print("=" * 62)

    cfg = check_config()
    if cfg:
        plat = check_platform(cfg)
        if plat:
            check_credentials(plat)
        check_provider(cfg)
    check_plugin()
    check_fonts()
    check_port()

    failed = sum(1 for level, _, _ in results if level == FAIL)
    warned = sum(1 for level, _, _ in results if level == WARN)
    print("=" * 62)
    print(f"通过 {len(results) - failed - warned} 项，警告 {warned} 项，失败 {failed} 项")
    if failed:
        print("有失败项，先全部解决再接群。")
    elif warned:
        print("没有硬性失败。警告项建议看一下，尤其是字体。")
    else:
        print("全部通过。可以 ./run.sh 启动，然后在群里发 q。")
    print()
    print("平台后台还需要确认（这些本地查不到）：")
    print("  1. IP 白名单里加上服务器的公网 IP")
    print("  2. 群设置 → 「获取群内全部消息」打开，否则只能收到 @")
    print("  3. 机器人已在目标群里（且你是群主）")
    print("  4. 若在沙箱环境，需要在后台把机器人发布到线上")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
