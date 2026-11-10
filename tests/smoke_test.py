#!/usr/bin/env python3
"""
KouHuBot 单元测试（不联网、不出图、不调用模型）

这里只测「纯函数 + 数据库」，跑一遍一两秒。
端到端的完整流程（出图、判题、计分、换题）在 local_harness.py 里。

    .local/venv/bin/python tests/smoke_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _astrbot_stub as stub  # noqa: E402

_FAILS: list[str] = []
_COUNT = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _COUNT
    _COUNT += 1
    if ok:
        print(f"  ✅ {name}")
    else:
        _FAILS.append(name)
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def eq(name: str, got, want) -> None:
    check(name, got == want, f"实际 {got!r}，期望 {want!r}")


# ----------------------------------------------------------------------
# 一份贴近真实的 CF 题面 HTML，用来做解析回归
# ----------------------------------------------------------------------
CF_HTML = """
<html><body>
<div class="problem-statement">
  <div class="header">
    <div class="title">A. Watermelon</div>
    <div class="time-limit"><div class="property-title">time limit per test</div>1 second</div>
    <div class="memory-limit"><div class="property-title">memory limit per test</div>64 megabytes</div>
    <div class="input-file"><div class="property-title">input</div>standard input</div>
    <div class="output-file"><div class="property-title">output</div>standard output</div>
  </div>
  <div>
    <p>One hot summer day Pete and his friend Billy decided to buy a watermelon.</p>
    <p>They chose the biggest one. It weighs $$$w$$$ kilograms.</p>
  </div>
  <div class="input-specification">
    <div class="section-title">Input</div>
    <p>The first line contains integer $$$w$$$ ($$$1 \\le w \\le 100$$$).</p>
  </div>
  <div class="output-specification">
    <div class="section-title">Output</div>
    <p>Print YES if they can divide it, otherwise NO.</p>
  </div>
  <div class="sample-tests">
    <div class="sample-test">
      <div class="input"><div class="title">Input</div><pre>8</pre></div>
      <div class="output"><div class="title">Output</div><pre>YES</pre></div>
    </div>
  </div>
</div>
</body></html>
"""


def test_scoring(scoring) -> None:
    print("\n[scoring] 档位与分值")
    eq("tier_of(1234) → 1200", scoring.tier_of(1234), 1200)
    eq("tier_of(800) → 800", scoring.tier_of(800), 800)
    eq("tier_of(799) 落到最低档", scoring.tier_of(799), scoring.LOWEST_TIER)
    eq("tier_of(4000) 落到最高档", scoring.tier_of(4000), scoring.HIGHEST_TIER)
    eq("tier_of(None) 用默认档", scoring.tier_of(None), scoring.DEFAULT_TIER)
    eq("points_for(1200)", scoring.points_for(1200), 220)
    eq("points_for(2400)", scoring.points_for(2400), 1500)

    # 分值必须单调递增，否则「越难越值钱」就破了
    tiers = sorted(scoring.TIER_POINTS)
    values = [scoring.TIER_POINTS[t] for t in tiers]
    check("分值随难度单调递增", all(b > a for a, b in zip(values, values[1:], strict=False)))

    eq("shift_tier 上移 1 档", scoring.shift_tier(1200, 1), 1400)
    eq("shift_tier 下移 1 档", scoring.shift_tier(1200, -1), 1000)
    eq("shift_tier 不会越界", scoring.shift_tier(scoring.LOWEST_TIER, -5), scoring.LOWEST_TIER)

    tier, why = scoring.next_tier(1200, solved=7, issued=20)
    eq("解出率 35% → 升档", tier, 1400)
    check("升档说明含「升档」", "升档" in why, why)
    tier, why = scoring.next_tier(1200, solved=1, issued=20)
    eq("解出率 5% → 降档", tier, 1000)
    check("降档说明含「降档」", "降档" in why, why)
    tier, _ = scoring.next_tier(1200, solved=4, issued=20)
    eq("解出率 20% → 不动", tier, 1200)
    tier, _ = scoring.next_tier(scoring.HIGHEST_TIER, solved=20, issued=20)
    eq("最高档不能再升", tier, scoring.HIGHEST_TIER)

    # 反刷分：堆低档题的总收益必须远低于打高档题
    low_grind = 100 * scoring.points_for(800)
    mid_30 = 30 * scoring.points_for(1600)
    high_40 = 40 * scoring.points_for(2600)
    check(
        f"刷 100 道简单({low_grind}) < 打 30 道中级({mid_30})",
        low_grind < mid_30,
    )
    # 反刷分的实质：想靠堆简单题追上高级题，需要多打多少道
    equivalent = high_40 / scoring.points_for(800)
    check(
        f"40 道高级({high_40}) ≈ 刷 {equivalent:.0f} 道简单题，堆题不可行",
        equivalent > 500,
    )


def test_tex(cf_mod) -> None:
    print("\n[tex] 公式处理")
    eq("\\le 归一化成 \\leq", cf_mod.normalize_tex(r"a \le b"), r"a \leq b")
    eq("\\ge 归一化成 \\geq", cf_mod.normalize_tex(r"a \ge b"), r"a \geq b")
    eq("\\lt 归一化成 <", cf_mod.normalize_tex(r"a \lt b"), "a < b")
    eq("\\dots 归一化成 \\ldots", cf_mod.normalize_tex(r"a \dots b"), r"a \ldots b")
    eq("\\text 归一化成 \\mathrm", cf_mod.normalize_tex(r"\text{YES}"), r"\mathrm{YES}")

    text = r"求 $$$1 \le q \le 10^5$$$ 和 $$$n$$$ 的关系"
    protected, found = cf_mod.protect_tex(text)
    eq("占位符替换掉全部公式", found.__len__(), 2)
    check("正文里不再有 $$$", "$$$" not in protected, protected)

    restored = cf_mod.restore_tex(protected, found)
    check("还原后公式回来了", "$$$1 \\le q \\le 10^5$$$" in restored, restored)
    check("公式数量守恒", cf_mod.tex_intact(restored, 2))

    # 模型可能把 ⟦0⟧ 写成 [0]、【0】、[[0]]，都要认
    for fake in ("[{0}]", "【{0}】", "[[{0}]]", "[ {0} ]"):
        variant = fake.format(0)
        got = cf_mod.restore_tex(f"值是 {variant}", found)
        check(f"还原容错 {variant}", "$$$" in got, got)

    # 译文把公式弄丢时要能被发现（上层据此退回英文）
    check("丢公式能被检出", not cf_mod.tex_intact("译文里没有公式了", 2))


def test_parse_statement(cf_mod) -> None:
    print("\n[parse] CF 题面解析")
    p = cf_mod.parse_statement(CF_HTML)
    eq("标题", p["title"], "A. Watermelon")
    eq("时间限制", p["time_limit"], 1.0)
    eq("内存限制", p["memory_limit"], 64)
    check("正文含题面内容", "watermelon" in p["statement_en"], p["statement_en"][:80])
    check("正文保留了 LaTeX", "$$$w$$$" in p["statement_en"], p["statement_en"])
    eq("样例条数", len(p["examples"]), 1)
    eq("样例题入", p["examples"][0]["input"], "8")
    eq("样例输出", p["examples"][0]["output"], "YES")
    check(
        "输入段落去掉了重复的 Input 标题",
        not p["statement_en"].lstrip().startswith("Input"),
        p["statement_en"][:40],
    )


def test_parse_verdict(mod) -> None:
    print("\n[verdict] 判定解析")
    r = mod.parse_verdict('{"verdict": "AC", "comment": "对"}')
    eq("标准 JSON", r["verdict"], "AC")
    r = mod.parse_verdict('```json\n{"verdict":"WA","comment":"错"}\n```')
    eq("带代码块围栏", r["verdict"], "WA")
    r = mod.parse_verdict('判定结果：{"verdict":"ac","comment":"小写也行"}')
    eq("小写判定", r["verdict"], "AC")

    # 模型跑偏成 PARTIAL 时，算法题必须落到 WA，不能白送分
    r = mod.parse_verdict('{"verdict":"PARTIAL","comment":"x"}', mod.JUDGE_VERDICTS)
    check("算法题不接受 PARTIAL", r is None or r["verdict"] in mod.JUDGE_VERDICTS, str(r))

    r = mod.parse_verdict("我觉得这题应该是 AC 吧", mod.JUDGE_VERDICTS)
    eq("非 JSON 兜底", r["verdict"], "AC")
    check("完全无法识别返回 None", mod.parse_verdict("？？？", mod.JUDGE_VERDICTS) is None)


def test_judge_prompt(mod) -> None:
    print("\n[judge] 判题提示词")
    prompt = mod.build_judge_prompt(
        {
            "title": "D. Distinct Characters Queries",
            "statement_en": "You are given a string s and q queries.",
            "time_limit": 2.0,
            "memory_limit": 256,
        },
        "我用两层循环",
    )
    check("带时间限制", "2.0 秒" in prompt)
    check("带内存限制", "256 MB" in prompt)
    check("强制先算复杂度", "复杂度必须先算" in prompt)
    check("要求对比时限", "时间限制比较" in prompt)
    check("只有 AC/WA", "PARTIAL" not in prompt)
    check("作答被包进隔离标签", "<student_answer>" in prompt and "</student_answer>" in prompt)
    check("声明「我 AC 了」不算证据", "已经 AC" in prompt or "不算证据" in prompt)


def test_cache(cf_mod, tmp: Path) -> None:
    print("\n[cache] 题面缓存")
    cache = cf_mod.CfCache(tmp / "cf_cache.db")
    eq("空库可用题数为 0", cache.ready_count(), 0)
    check("空库取不到东西", cache.get("4A") is None)

    cache.save(
        {
            "cf_key": "4A", "contest_id": 4, "idx": "A", "rating": 800,
            "tier": 800, "points": 100, "title": "A. Watermelon",
            "statement_en": "text", "statement_cn": "译文",
            "examples": [{"input": "8", "output": "YES"}],
            "time_limit": 1.0, "memory_limit": 64,
        }
    )
    eq("存进去后可用题数 +1", cache.ready_count(), 1)
    row = cache.get("4A")
    eq("取得回来", row["title"], "A. Watermelon")
    eq("样例还原成列表", json.loads(row["examples_json"])[0]["input"], "8")

    # 重复写入必须是更新而不是报错；译文为空时不能把已有译文冲掉
    cache.save({"cf_key": "4A", "title": "改名了", "statement_cn": None})
    row = cache.get("4A")
    eq("标题已更新", row["title"], "改名了")
    eq("译文没被空值冲掉", row["statement_cn"], "译文")

    cache.bump_used("4A")
    eq("使用次数可累加", cache.get("4A")["used_count"], 1)
    eq("known_keys", cache.known_keys(), {"4A"})
    cache.close()


def test_store(mod, tmp: Path) -> None:
    print("\n[store] 积分库")
    store = mod.OjStore(tmp / "kouhu_oj.db")
    scope = "group:1"

    eq("新群默认档位 1200", store.state(scope)["current_tier"], 1200)
    check("新人是新加入", store.join(scope, "u1", "张三") is True)
    check("重复 join 不算新", store.join(scope, "u1", "张三") is False)
    check("报名后可查到", store.player(scope, "u1") is not None)
    check("没报名查不到", store.player(scope, "u2") is None)

    problem = {"cf_key": "4A", "tier": 800, "points": 100, "title": "A"}
    store.open_problem(scope, problem, "u1")
    cur = store.current(scope)
    eq("当前题状态 open", cur["status"], "open")
    eq("已记录派过的题", store.used_keys(scope), {"4A"})

    eq("第一次提交编号 1", store.next_attempt_no(scope, "4A", "u1"), 1)
    store.record_attempt(scope, "4A", "u1", 1, "WA")
    eq("第二次提交编号 2", store.next_attempt_no(scope, "4A", "u1"), 2)

    store.close_problem(scope, "ac", "u1")
    eq("关题后状态 ac", store.current(scope)["status"], "ac")

    check("首次记分成功", store.mark_solved(scope, "u1", "4A", 100) is True)
    check("同一题重复记分被拒", store.mark_solved(scope, "u1", "4A", 100) is False)
    store.add_score(scope, "u1", 100, first_try=True)
    p = store.player(scope, "u1")
    eq("积分入账", p["total_score"], 100)
    eq("解题数", p["solved"], 1)

    board = store.leaderboard(scope, 10)
    eq("榜单有 1 人", len(board), 1)
    eq("榜单字段 total", board[0]["total"], 100)
    check("榜单字段 tried 存在", "tried" in board[0])

    # 换题：同一 cf_key 不能再被派；换新题后当前题要重置为 open
    eq("派过的题会挡住重复派题", "4A" in store.used_keys(scope), True)
    store.open_problem(scope, {"cf_key": "1000A", "tier": 1000, "points": 150, "title": "B"}, "u1")
    cur = store.current(scope)
    eq("换题后 cf_key 变了", cur["cf_key"], "1000A")
    eq("换题后重新 open", cur["status"], "open")
    check("换题清掉了上一题的关闭时间", cur["closed_at"] is None)

    store.set_tier(scope, 1600)
    eq("改档生效", store.state(scope)["current_tier"], 1600)

    # 迁移：换个版本号重开，旧表要被清掉，但不能报错
    store.close()
    store2 = mod.OjStore(tmp / "kouhu_oj.db")
    eq("重开后数据还在", store2.player(scope, "u1")["total_score"], 100)
    store2.close()


def test_package_import(tmp: Path) -> None:
    """按 AstrBot 的方式以「包」加载插件。

    线上插件名是 data.plugins.astrbot_plugin_kouhu_oj，兄弟模块只能用相对导入。
    之前 render.py 里写了 from sources.codeforces import ...，在包加载下会失败，
    而失败被 tex_image 的 try/except 吞掉——表现是公式**静默**退化成 LaTeX 源码。
    这条测试专门盯住它。
    """
    print("\n[package] 以包方式加载（模拟 AstrBot）")
    import importlib
    import shutil

    root = tmp / "pkgroot"
    shutil.copytree(
        Path(__file__).resolve().parent.parent / "plugins" / "astrbot_plugin_kouhu_oj",
        root / "data" / "plugins" / "astrbot_plugin_kouhu_oj",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # 关键：只放外层目录，**不放**插件目录，逼出所有绝对导入问题
    sys.path.insert(0, str(root))
    try:
        render = importlib.import_module("data.plugins.astrbot_plugin_kouhu_oj.render")
        check("render 可以包加载", True)
        img = render.tex_image(r"1 \le q \le 10^5")
        check("包加载下公式仍能渲染", img is not None, "退化成源码了")

        cf = importlib.import_module("data.plugins.astrbot_plugin_kouhu_oj.sources.codeforces")
        eq("包加载下 normalize_tex 生效", cf.normalize_tex(r"a \le b"), r"a \leq b")

        main_mod = importlib.import_module("data.plugins.astrbot_plugin_kouhu_oj.main")
        eq("包加载下 main 可导入", main_mod.PLUGIN_VERSION, "0.5.0")
        cmds = [n for n in dir(main_mod.KouHuOJ) if n.startswith("cmd_")]
        check(f"指令齐全（{len(cmds)} 个）", len(cmds) >= 13, str(sorted(cmds)))
    finally:
        sys.path.remove(str(root))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kouhu_unit_"))
    mod = stub.load_plugin(data_root=tmp)

    import scoring

    from sources import codeforces as cf_mod

    test_scoring(scoring)
    test_tex(cf_mod)
    test_parse_statement(cf_mod)
    test_parse_verdict(mod)
    test_judge_prompt(mod)
    test_cache(cf_mod, tmp)
    test_store(mod, tmp)
    test_package_import(tmp)

    print(f"\n{'─' * 60}")
    if _FAILS:
        print(f"❌ {len(_FAILS)} / {_COUNT} 项失败：")
        for name in _FAILS:
            print(f"   · {name}")
        return 1
    print(f"✅ 全部通过（{_COUNT} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
