"""真实 AstrBot 集成测试。

启动一个真实的 AstrBot 实例（真实的插件加载器、事件管线、Context、
渲染和 SQLite），用一个假平台适配器注入群消息并收集回复。

和 tests/local_harness.py 的区别：那边的 Context / 事件 / 模型全是假的，
和真实 API 形状不一致时会骗过测试（漏 await 就是这么漏过去的）；
这里除了消息出入口，其余全是真实代码。

    .local/venv/bin/python tests/integration/run.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASTRBOT = REPO / ".local" / "astrbot"
VENV_PY = REPO / ".local" / "venv" / "bin" / "python"
PLUGIN_SRC = REPO / "plugins" / "astrbot_plugin_kouhu_oj"
MAIN_LOCAL = REPO / ".local" / "astrbot"
WORK = REPO / ".local" / "itest"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_root(run_dir: Path) -> tuple[Path, Path, Path, Path]:
    """准备一个独立的 AstrBot 数据目录。"""
    root = run_dir / "root"
    (root / "data" / "plugins").mkdir(parents=True)

    # 复制现有配置（含模型提供商配置），只换平台和端口
    cfg = json.loads((MAIN_LOCAL / "data" / "cmd_config.json").read_text(encoding="utf-8-sig"))
    scenario = run_dir / "scenario.json"
    result = run_dir / "result.json"
    cfg["platform"] = [
        {
            "type": "kouhu_test",
            "enable": True,
            "id": "kouhu_test",
            "scenario": str(scenario),
            "result_path": str(result),
        }
    ]
    cfg.setdefault("dashboard", {})["port"] = free_port()
    (root / "data" / "cmd_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 插件配置：关掉抽题冷却，否则连续抽题会互相干扰
    plugin_cfg: dict = {}
    plugin_cfg_src = MAIN_LOCAL / "data" / "config" / "astrbot_plugin_kouhu_oj_config.json"
    if plugin_cfg_src.is_file():
        plugin_cfg = json.loads(plugin_cfg_src.read_text(encoding="utf-8-sig"))
    plugin_cfg["new_cooldown_seconds"] = 0
    plugin_cfg["ask_cooldown_seconds"] = 0
    (root / "data" / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "config" / "astrbot_plugin_kouhu_oj_config.json").write_text(
        json.dumps(plugin_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    shutil.copytree(
        PLUGIN_SRC,
        root / "data" / "plugins" / PLUGIN_SRC.name,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return root, scenario, result, run_dir / "astrbot.log"


def seed_broken_db(root: Path) -> None:
    """故意造一个「版本号已是当前版本、表结构却是 ELO 时代老表」的库。

    线上就是被这个坑过一次：迁移的 DROP 列表漏了 current_problem，
    版本号改了但表没重建，插入直接崩。这个用例保证以后不会再犯：
    哪怕版本号对得上，只要老表还在，启动时也必须清掉并建出新表。
    """
    sys.path.insert(0, str(PLUGIN_SRC))
    plugin_dir = root / "data" / "plugin_data" / PLUGIN_SRC.name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(plugin_dir / "kouhu_oj.db"))
    conn.execute(
        """CREATE TABLE current_problem (
            scope_id TEXT PRIMARY KEY, slug TEXT NOT NULL, frontend_id TEXT,
            title TEXT NOT NULL, difficulty TEXT, content_html TEXT, link TEXT,
            issued_at REAL NOT NULL, issued_by TEXT)"""
    )
    conn.execute(
        """CREATE TABLE progress (
            scope_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT,
            slug TEXT NOT NULL, attempts INTEGER DEFAULT 0, best_score INTEGER DEFAULT 0,
            solved INTEGER DEFAULT 0, gave_up INTEGER DEFAULT 0, last_at REAL)"""
    )
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()


SCENARIO = [
    {"text": "help", "user": "u1", "name": "小明", "settle": 1.5},
    {"text": "join", "user": "u1", "name": "小明", "settle": 1.5},
    {"text": "new", "user": "u1", "name": "小明", "timeout": 180, "settle": 4.0},
    {"text": "problem", "user": "u1", "name": "小明", "settle": 2.0},
    {
        "text": "submit 我用 26 棵树状数组，每棵维护一个字符的前缀和，"
                "修改 O(log n)，询问对 26 个字符各查一次，总复杂度 O(26 q log n)",
        "user": "u1",
        "name": "小明",
        "timeout": 150,
        "settle": 3.0,
    },
    {"text": "grammar", "user": "u2", "name": "小红", "timeout": 60, "settle": 2.5},
    {
        "text": "submit 这里少了标记已访问，会重复入队",
        "user": "u2",
        "name": "小红",
        "timeout": 150,
        "settle": 3.0,
    },
    {"text": "rank", "user": "u1", "name": "小明", "settle": 1.5},
    {"text": "me", "user": "u1", "name": "小明", "settle": 1.5},
    {
        "text": "ask 题库现在是什么样的，运行环境如何",
        "user": "u1",
        "name": "小明",
        "timeout": 150,
        "settle": 3.0,
        "expect_replies": 2,  # ack + 真正的回答，只收到 ack 不算过
    },
]

# 每一步期望出现的内容
EXPECT = {
    0: {"name": "help 帮助", "contains": ["new", "submit", "giveup", "rank"], "images": 0},
    1: {"name": "join 报名", "contains": ["报名成功"], "images": 0},
    # 派题要出两张图：题面正文 + 样例
    2: {"name": "new 派题（两张图）", "contains": ["分"], "images": 2},
    3: {"name": "problem 重看（两张图）", "contains": [], "images": 2},
    # 判定结果由真实模型给出，AC 或 WA 都算通过，重点是链路不崩
    4: {"name": "submit 判题", "contains": [], "images": 0, "min_len": 5},
    5: {"name": "grammar 坑点", "contains": [], "images": 1},
    6: {"name": "submit 坑点题", "contains": [], "images": 0, "min_len": 5},
    7: {"name": "rank 排行榜", "contains": ["题库缓存"], "images": 1},
    8: {"name": "me 战绩", "contains": ["积分", "解题"], "images": 0},
    9: {
        "name": "ask 自由问答",
        "contains": [],
        "images": 0,
        "min_records": 2,
        "min_len": 20,
    },
}


def wait_result(result: Path, proc: subprocess.Popen, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result.is_file():
            try:
                data = json.loads(result.read_text(encoding="utf-8"))
                if data.get("done"):
                    return data
            except (ValueError, json.JSONDecodeError):
                pass
        if proc.poll() is not None:
            if result.is_file():
                try:
                    return json.loads(result.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    return None
            return None
        time.sleep(0.5)
    return None


def kill_tree(proc: subprocess.Popen) -> None:
    """连同子进程一起杀掉。AstrBot 会自己起子任务，只 terminate 父进程不够。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def main() -> int:
    if not VENV_PY.is_file():
        print(f"{RED}缺少虚拟环境：{VENV_PY}{RESET}")
        return 1

    # 只保留最近 5 轮，避免反复调试堆积一堆目录
    WORK.mkdir(parents=True, exist_ok=True)
    old_runs = sorted(p for p in WORK.iterdir() if p.is_dir() and p.name.startswith("20"))
    for stale in old_runs[:-5]:
        shutil.rmtree(stale, ignore_errors=True)

    run_dir = WORK / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    root, scenario, result, log_path = build_root(run_dir)
    scenario.write_text(json.dumps(SCENARIO, ensure_ascii=False, indent=2), encoding="utf-8")
    seed_broken_db(root)

    env = dict(os.environ, ASTRBOT_ROOT=str(root), PYTHONUNBUFFERED="1")
    print(f"{DIM}启动真实 AstrBot（{run_dir}）...{RESET}")
    proc = subprocess.Popen(
        [str(VENV_PY), "-u", str(Path(__file__).parent / "boot.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    log_fh = open(log_path, "w", encoding="utf-8")

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            log_fh.write(line)
            log_fh.flush()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        data = wait_result(result, proc, timeout=420)
    finally:
        kill_tree(proc)
        reader.join(timeout=5)
        log_fh.close()

    if not data:
        print(f"{RED}❌ 没拿到测试结果，AstrBot 可能没起来{RESET}")
        print(f"   日志：{log_path}")
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]:
            print(f"   {DIM}{line}{RESET}")
        return 1

    by_step: dict[int, list[dict]] = {}
    for rec in data.get("records", []):
        by_step.setdefault(rec.get("step", -2), []).append(rec)

    print()
    failures = 0
    for index, _step in enumerate(SCENARIO):
        expect = EXPECT.get(index)
        if not expect:
            continue
        group = by_step.get(index, [])
        texts = "\n".join(r.get("text", "") for r in group)
        images = sum(len(r.get("images", [])) for r in group)
        problems: list[str] = []
        if not group:
            problems.append("没有回复")
        for needle in expect["contains"]:
            if needle not in texts:
                problems.append(f"缺少「{needle}」")
        if expect["images"] and images < expect["images"]:
            problems.append(f"期望 {expect['images']} 张图，实际 {images}")
        if expect.get("min_records") and len(group) < expect["min_records"]:
            problems.append(f"期望至少 {expect['min_records']} 条回复，实际 {len(group)}")
        if expect.get("min_len") and not any(
            len(r.get("text") or "") >= expect["min_len"] for r in group
        ):
            problems.append(f"回复都没有超过 {expect['min_len']} 字（可能只有 ack）")
        label = f"{index}. {expect['name']}"
        if problems:
            failures += 1
            print(f"{RED}❌ {label}：{'；'.join(problems)}{RESET}")
            for rec in group:
                snippet = (rec.get("text") or "").replace("\n", " ")[:100]
                print(f"   {DIM}↳ {snippet!r} imgs={rec.get('images')}{RESET}")
        else:
            lines = (group[0].get("text") or "").splitlines()
            first = lines[0][:70] if lines else "[图片]"
            print(f"{GREEN}✅ {label}{RESET}  {DIM}{first}{RESET}")

    print()
    if failures:
        print(f"{RED}❌ 集成测试失败：{failures}/{len(EXPECT)} 步未通过{RESET}")
        print(f"   日志：{log_path}")
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]:
            print(f"   {DIM}{line}{RESET}")
        return 1

    print(f"{GREEN}🎉 真实 AstrBot 集成测试全部通过（{len(EXPECT)} 步）{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
