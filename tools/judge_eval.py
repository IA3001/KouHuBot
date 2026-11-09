#!/usr/bin/env python3
"""
判题质量评估工具

用真实大模型跑一组预置作答，检查模型返回的 verdict 是否与预期一致，
同时统计「JSON 解析成功率」——这是判题可靠性的两个关键指标。

用法一（从 AstrBot 配置自动读取模型）：
    python3 tools/judge_eval.py --config .local/astrbot/data/cmd_config.json

用法二（手动指定，OpenAI 兼容接口）：
    KOUHU_LLM_BASE_URL=https://api.example.com/v1 \
    KOUHU_LLM_API_KEY=sk-xxx \
    KOUHU_LLM_MODEL=some-model \
    python3 tools/judge_eval.py

退出码：0 表示全部符合预期，1 表示存在偏差。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

import _astrbot_stub as stub  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# 预置测试用例：期望值与题目 rubric 对齐
# ---------------------------------------------------------------------------
CASES: list[dict[str, str]] = [
    {
        "pid": "KH-005",
        "label": "充分正确的作答",
        "expect": "AC",
        "submission": (
            "换门中奖概率是 2/3。因为最初选中的概率只有 1/3，也就是说选错的概率是 2/3。"
            "主持人打开一扇没有奖品的门，并不会改变「你最初选错」这个事实，"
            "而换门等价于押「最初选错」，所以换门中奖概率是 2/3，不换只有 1/3。"
        ),
    },
    {
        "pid": "KH-005",
        "label": "经典错误答案 1/2",
        "expect": "WA",
        "submission": "换不换都一样，都是 1/2。因为主持人排除一个错误选项后，剩下两扇门必有一扇有奖，所以是二分之一。",
    },
    {
        "pid": "KH-005",
        "label": "只给结论无推导",
        "expect": "PARTIAL",
        "submission": "应该是 2/3 吧。",
    },
    {
        "pid": "KH-003",
        "label": "正确答案 25 人 + 理由",
        "expect": "AC",
        "submission": (
            "至少 25 个人。考虑最坏情况：每个月恰好各有 2 个人过生日，"
            "12 个月总共可以容纳 12×2=24 个人，此时仍然没有三人同月。"
            "因此第 25 个人一出现，必然使某个月的人数达到 3 人。"
        ),
    },
    {
        "pid": "KH-003",
        "label": "错误答案 13 人",
        "expect": "WA",
        "submission": "13 个人就够了，抽屉原理嘛，13 个人放 12 个月肯定有重复。",
    },
    {
        "pid": "KH-004",
        "label": "正确三指针迭代",
        "expect": "AC",
        "submission": (
            "用三个指针 prev=null、cur=head。循环中先 nxt=cur.next 暂存后继，"
            "再 cur.next=prev，然后 prev=cur、cur=nxt，直到 cur 为空，返回 prev。"
            "只用了常数个指针，空间复杂度 O(1)。"
        ),
    },
    {
        "pid": "KH-004",
        "label": "递归解法却声称 O(1)",
        "expect": "PARTIAL",
        "submission": "用递归反转：先 reverse(head.next)，再让 head.next.next = head，head.next = null。空间复杂度 O(1)。",
    },
    {
        "pid": "KH-001",
        "label": "纯敷衍（应判 WA）",
        "expect": "WA",
        "submission": "不知道，给点面子吧，这题我不会。",
    },
    {
        "pid": "KH-001",
        "label": "只报定理名无推导",
        "expect": "PARTIAL",
        "submission": "因为抽屉原理，所以一定有两个人生日在同一个月。",
    },
]


def load_config(path: Path) -> tuple[str, str, str]:
    """从 AstrBot 的 cmd_config.json 读取 base_url / key / model。"""
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    sources = {s["id"]: s for s in cfg.get("provider_sources", [])}
    provider = cfg["provider"][0]
    src = sources[provider["provider_source_id"]]
    key = src["key"]
    if isinstance(key, list):
        key = key[0] if key else ""
    return src["api_base"], key, provider["model"]


def call_llm(base_url: str, key: str, model: str, prompt: str, timeout: int = 90) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        return f"__HTTP_ERROR__ {exc.code}: {exc.read().decode()[:200]}"
    except Exception as exc:  # noqa: BLE001
        return f"__CALL_ERROR__ {type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, help="AstrBot cmd_config.json 路径")
    parser.add_argument("--model", help="覆盖模型名")
    args = parser.parse_args()

    if args.config:
        base_url, key, model = load_config(args.config)
    else:
        import os

        base_url = os.getenv("KOUHU_LLM_BASE_URL", "").strip()
        key = os.getenv("KOUHU_LLM_API_KEY", "").strip()
        model = os.getenv("KOUHU_LLM_MODEL", "").strip()
        if not (base_url and key and model):
            print("缺少模型配置：用 --config 指定 AstrBot 配置，或设置 KOUHU_LLM_* 环境变量")
            return 1

    if args.model:
        model = args.model

    mod = stub.load_plugin(data_root=REPO_ROOT / ".local" / "judge_eval_data")
    problems = {p["pid"]: p for p in json.loads(
        (REPO_ROOT / "plugins" / "astrbot_plugin_kouhu_oj" / "problems.json").read_text(encoding="utf-8")
    )}

    print(f"模型     : {model}")
    print(f"端点     : {base_url}")
    print(f"用例数   : {len(CASES)}\n")
    print(f"{'题目':8} {'用例':22} {'期望':9} {'实际':9} {'得分':5} 解析 判定")
    print("─" * 88)

    hit = parsed_ok = 0
    for case in CASES:
        problem = problems[case["pid"]]
        prompt = mod.build_judge_prompt(
            title=problem["title"],
            statement=problem["statement"],
            answer=problem["answer"],
            rubric=problem["rubric"],
            submission=case["submission"],
        )
        raw = call_llm(base_url, key, model, prompt)

        if raw.startswith("__"):
            print(f"{case['pid']:8} {case['label']:22} {case['expect']:9} {'调用失败':9}")
            print(f"         └─ {raw[:150]}")
            continue

        result = mod.parse_verdict(raw)
        if result is None:
            print(f"{case['pid']:8} {case['label']:22} {case['expect']:9} {'解析失败':9} -     ❌   ❌")
            print(f"         └─ 原始输出: {raw[:150]!r}")
            continue

        parsed_ok += 1
        match = result["verdict"] == case["expect"]
        if match:
            hit += 1
        print(
            f"{case['pid']:8} {case['label']:22} {case['expect']:9} "
            f"{result['verdict']:9} {result['score']:<5} ✅   {'✅' if match else '❌'}"
        )
        print(f"         └─ {result['comment'][:80]}")

    total = len(CASES)
    print("─" * 88)
    print(f"JSON 解析成功率 : {parsed_ok}/{total}  ({parsed_ok / total * 100:.0f}%)")
    print(f"判定符合预期    : {hit}/{total}  ({hit / total * 100:.0f}%)")
    return 0 if hit == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
