#!/usr/bin/env python3
"""
判题质量评估工具

用真实大模型跑一组预置作答，检查返回的 verdict 是否符合预期，
同时统计「JSON 解析成功率」——这是判题可靠性的两个关键指标。

题目现在从 LeetCode 实时拉取，所以这里把题面和标题写死在用例里，
不依赖网络，换模型时可以直接横向对比。

用法一（从 AstrBot 配置自动读取模型）：
    .local/venv/bin/python tools/judge_eval.py --config .local/astrbot/data/cmd_config.json

用法二（手动指定，OpenAI 兼容接口）：
    KOUHU_LLM_BASE_URL=https://api.example.com/v1 \
    KOUHU_LLM_API_KEY=sk-xxx \
    KOUHU_LLM_MODEL=some-model \
    .local/venv/bin/python tools/judge_eval.py

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

TWO_SUM = (
    "给定一个整数数组 nums 和一个整数目标值 target，请你在该数组中找出和为目标值 target 的"
    "那两个整数，并返回它们的数组下标。你可以假设每种输入只会对应一个答案，"
    "并且你不能使用两次相同的元素。你可以按任意顺序返回答案。"
    "示例：nums = [2,7,11,15], target = 9，返回 [0,1]。"
)

REVERSE_LIST = (
    "给你单链表的头节点 head，请你反转链表，并返回反转后的链表。"
    "示例：输入 1->2->3->4->5，输出 5->4->3->2->1。"
)

LRU = (
    "请你设计并实现一个满足 LRU（最近最少使用）缓存约束的数据结构。"
    "实现 LRUCache 类：LRUCache(int capacity) 以正整数作为容量初始化；"
    "int get(int key) 如果关键字存在则返回其值，否则返回 -1；"
    "void put(int key, int value) 如果关键字已存在则变更其值，否则插入。"
    "如果插入导致数量超过 capacity，则逐出最久未使用的关键字。"
    "函数 get 和 put 必须以 O(1) 的平均时间复杂度运行。"
)

CASES: list[dict] = [
    {
        "title": "两数之和",
        "statement": TWO_SUM,
        "label": "哈希表一次遍历（最优解）",
        "expect": "AC",
        "submission": (
            "建一个哈希表存 值->下标，遍历数组，对每个 nums[i] 先查 target-nums[i] "
            "是否在表里，在就返回两个下标，不在就把 nums[i] 存进去。"
            "时间复杂度 O(n)，空间 O(n)。注意要先查再存，避免同一个元素用两次。"
        ),
    },
    {
        "title": "两数之和",
        "statement": TWO_SUM,
        "label": "暴力双重循环（正确解，题目不要求最优）",
        "expect": "AC",
        "submission": "两层 for 循环枚举所有 i<j，判断 nums[i]+nums[j] 是否等于 target，等于就返回 [i,j]。时间复杂度 O(n^2)。",
    },
    {
        "title": "两数之和",
        "statement": TWO_SUM,
        "label": "排序双指针却返回了值而非下标",
        "expect": "PARTIAL",
        "submission": "先排序，然后用双指针从两端往中间夹，和大了右指针左移，和小了左指针右移，找到就返回这两个数。复杂度 O(n log n)。",
    },
    {
        "title": "两数之和",
        "statement": TWO_SUM,
        "label": "纯敷衍",
        "expect": "WA",
        "submission": "不会，随便蒙一个吧，反正答案就两个数。",
    },
    {
        "title": "反转链表",
        "statement": REVERSE_LIST,
        "label": "三指针迭代（标准解）",
        "expect": "AC",
        "submission": (
            "用 prev=null、cur=head 两个指针，循环里先存 nxt=cur.next，"
            "再让 cur.next=prev，然后 prev=cur、cur=nxt，直到 cur 为空，返回 prev。"
            "只用了常数个指针，时间 O(n)，空间 O(1)。"
        ),
    },
    {
        "title": "反转链表",
        "statement": REVERSE_LIST,
        "label": "递归解法却声称空间 O(1)",
        "expect": "PARTIAL",
        "submission": "递归做：先 reverse(head.next)，再让 head.next.next = head，head.next = null，返回新的头。空间复杂度 O(1)。",
    },
    {
        "title": "LRU 缓存",
        "statement": LRU,
        "label": "哈希表 + 双向链表（标准解）",
        "expect": "AC",
        "submission": (
            "用哈希表存 key 到链表节点的映射，再维护一个双向链表按访问顺序排列："
            "最近访问的放头部，最久未使用的在尾部。get 命中时把节点移到头部并返回值；"
            "put 时若 key 存在则更新值并移到头部，若不存在则新建节点插到头部，"
            "容量超了就删掉尾部节点并同步删除哈希表项。两个操作都是 O(1)。"
        ),
    },
    {
        "title": "LRU 缓存",
        "statement": LRU,
        "label": "数组 + 时间戳（违反 O(1) 要求）",
        "expect": "WA",
        "submission": "用一个数组存 key 和 value，每个元素再记一个最后访问时间戳，每次淘汰就遍历数组找时间戳最小的删掉。",
    },
]


PITFALL_CASES = [
    ("sort-comparator", "应该用 < 而不是 <=。std::sort 要求比较器是严格弱序，cmp(a,a) 必须为 false，用 <= 会让相等元素互相小于。", "AC"),
    ("sort-comparator", "比较器写错了，会让排序结果不对。", "PARTIAL"),
    ("sort-comparator", "应该换成 stable_sort 就没事了。", "WA"),
    ("scanf-no-amp", "scanf 的 %d 需要变量地址，应该写 &n，传 n 的值会被当成地址写入非法内存。", "AC"),
    ("scanf-no-amp", "scanf 那行有问题，读进来的值不对。", "PARTIAL"),
    ("scanf-no-amp", "printf 的格式符写错了，应该用 %ld。", "WA"),
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
    parser.add_argument("--pitfalls", action="store_true", help="跑坑点题判题用例")
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

    if args.pitfalls:
        from sources.pitfalls import PitfallSource

        src = PitfallSource()
        print(f"模型     : {model}\n坑点用例 : {len(PITFALL_CASES)}\n")
        hit = parsed = 0
        for pid, sub, expect in PITFALL_CASES:
            prompt = mod.build_pitfall_prompt(src.get(pid), sub)
            res = mod.parse_verdict(call_llm(base_url, key, model, prompt))
            if not res:
                print(f"{pid:22} {expect:9} 解析失败")
                continue
            parsed += 1
            match = res["verdict"] == expect
            hit += match
            print(f"{pid:22} {expect:9} {res['verdict']:9} {res['score']:<4} {'✅' if match else '❌'}")
            print(f"   └─ {res['comment'][:78]}")
        total = len(PITFALL_CASES)
        print("─" * 66)
        print(f"JSON 解析成功率 : {parsed}/{total}　判定符合预期 : {hit}/{total}")
        return 0 if hit == total else 1

    print(f"模型     : {model}")
    print(f"端点     : {base_url}")
    print(f"用例数   : {len(CASES)}\n")
    print(f"{'用例':30} {'期望':9} {'实际':9} {'得分':5} 解析 判定")
    print("─" * 80)

    hit = parsed_ok = 0
    for case in CASES:
        prompt = mod.build_judge_prompt(
            title=case["title"],
            statement=case["statement"],
            submission=case["submission"],
        )
        raw = call_llm(base_url, key, model, prompt)

        if raw.startswith("__"):
            print(f"{case['label']:30} {case['expect']:9} {'调用失败':9}")
            print(f"      └─ {raw[:150]}")
            continue

        result = mod.parse_verdict(raw)
        if result is None:
            print(f"{case['label']:30} {case['expect']:9} {'解析失败':9} -     ❌   ❌")
            print(f"      └─ 原始输出: {raw[:150]!r}")
            continue

        parsed_ok += 1
        match = result["verdict"] == case["expect"]
        if match:
            hit += 1
        print(
            f"{case['label']:30} {case['expect']:9} "
            f"{result['verdict']:9} {result['score']:<5} ✅   {'✅' if match else '❌'}"
        )
        print(f"      └─ {result['comment'][:80]}")

    total = len(CASES)
    print("─" * 80)
    print(f"JSON 解析成功率 : {parsed_ok}/{total}  ({parsed_ok / total * 100:.0f}%)")
    print(f"判定符合预期    : {hit}/{total}  ({hit / total * 100:.0f}%)")
    return 0 if hit == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
