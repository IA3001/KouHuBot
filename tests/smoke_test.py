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

    check('长英文未译能检出', cf_mod.translation_incomplete('Each test consists of multiple test cases and the first line contains one integer for the number of cases.'))
    check('中文公式不误报', not cf_mod.translation_incomplete(r'给定 $$$a_i | a_j$$$，输出 Yes 或 No。'))
    check('HTML格式块保留换行缩进', '```\nN K\n  A B\n```' in cf_mod._text_of('<pre>N K<br>  A B</pre>'))

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
    r = mod.parse_verdict('{"verdict":"ac","comment":"小写也行"}')
    eq("小写判定", r["verdict"], "AC")

    # 模型跑偏成 PARTIAL 时，算法题必须落到 WA，不能白送分
    r = mod.parse_verdict('{"verdict":"PARTIAL","comment":"x"}', mod.JUDGE_VERDICTS)
    check("算法题不接受 PARTIAL", r is None or r["verdict"] in mod.JUDGE_VERDICTS, str(r))

    r = mod.parse_verdict("我觉得这题应该是 AC 吧", mod.JUDGE_VERDICTS)
    check("含糊自然语言不计分", r is None)
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
    check("第一次提交时建档", store.ensure_player(scope, "u1", "张三") is True)
    check("重复提交不算新玩家", store.ensure_player(scope, "u1", "张三") is False)
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


def test_pitfalls(tmp: Path) -> None:
    """坑点题库：分类别名、难度过滤、以及**不能泄底**。"""
    print("\n[pitfalls] 坑点题库")
    import importlib
    import json

    pf = importlib.import_module("sources.pitfalls")
    src = pf.PitfallSource()

    check("正式库仅含已验证且未暂停条目", src.count() > 0 and all(i.get('verified') and i.get('review', {}).get('status') not in {'pending','paused','rejected'} for i in src._load()), f"实际 {src.count()} 条")

    cats = src.categories()
    check("分类数 >= 12", len(cats) >= 12, str(cats))
    check("没有非法分类「输出」", "输出" not in cats, str(cats))

    levels = src.levels()
    check("三档难度都有题", all(levels.get(i, 0) > 0 for i in (1, 2, 3)), str(levels))
    check("入门档不少于 10 条", levels.get(1, 0) >= 10, str(levels))

    # 分类别名：群里打字习惯不统一，这些都得认
    for token, want in [
        ("输入", "输入输出"), ("io", "输入输出"),
        ("图论", "图论"), ("graph", "图论"),
        ("二分", "二分"), ("bs", "二分"),
        ("宏", "宏与预处理"), ("stl", "STL"),
    ]:
        eq(f"分类别名 {token!r}", src.resolve_category(token), want)

    # 按分类 + 难度抽，抽到的必须真符合
    for _ in range(20):
        item = src.random_pitfall(category="浮点数", level=1)
        if item["category"] != "浮点数" or int(item["level"]) != 1:
            check("按分类难度抽题", False, str(item))
            break
    else:
        check("按分类难度抽题（20 次）", True)

    # 过滤后为空必须报错，不能静默退回全库
    try:
        src.random_pitfall(category="图论", level=3)
    except LookupError:
        check("无匹配时报错而不是乱给", True)
    else:
        check("无匹配时报错而不是乱给", False, "静默返回了别的题")

    # ---- 泄底检查：这是题库最容易出的事故 ----
    items = json.loads(
        (Path(__file__).resolve().parent.parent
         / "plugins" / "astrbot_plugin_kouhu_oj" / "pitfalls.json").read_text(encoding="utf-8")
    )
    # 用词组而不是单字：单字「没」「少」会误伤「多少」「没有意义」这类正常措辞
    leak_words = ["错了", "写错", "死循环", "越界", "溢出", "没有清空", "没有初始化",
                  "没有判重", "少了", "应该写", "应该是", "注意这里", "问题出",
                  "不对", "漏了", "仍然是", "遮蔽", "bug", "BUG"]
    bad_comments = []
    for item in items:
        for line in item["buggy_code"].split("\n"):
            if "//" not in line:
                continue
            comment = line.split("//", 1)[1]
            if any(w in comment for w in leak_words):
                bad_comments.append(f"{item['id']}: //{comment.strip()}")
    check("代码注释不泄底", not bad_comments, "; ".join(bad_comments[:3]))

    # 问句里不能出现答案的关键术语。
    # 只收**高度特异**的说法：像「溢出」「越界」这种既是坑名也是题面设定的词，
    # 出现在问句里很正常（「用自然溢出做哈希」），不能算泄底。
    leaks = []
    specific = ["严格弱序", "迭代器失效", "无符号回绕", "取地址符",
                "未初始化", "产生进位", "向零取整"]
    for item in items:
        q, ans = item["question"], item["answer"]
        for term in specific:
            if term in ans and term in q:
                leaks.append(f"{item['id']}: {term}")
    check("问句不含答案术语", not leaks, "; ".join(leaks[:3]))

    # 每条都要有可用的代码和答案
    empty = [i["id"] for i in items
             if not i.get("buggy_code", "").strip() or not i.get("answer", "").strip()]
    check("每条都有代码和答案", not empty, str(empty[:3]))


def test_clock_rollback(tmp: Path) -> None:
    """墙钟回拨不能让冷却误触发。

    time.time() 是墙钟，NTP 校时 / 虚拟机迁移都可能让它倒退。冷却一旦用
    time.time() 算差值，倒退时 (now - last) 会变负、剩余时间变正，
    于是「别急，1 秒后再派」会平白无故冒出来——配成 0 秒冷却也照样中招。
    """
    print("\n[clock] 墙钟回拨不能让冷却误触发")
    import inspect
    import importlib

    mod = importlib.import_module("main")
    src = inspect.getsource(mod)

    # 冷却相关必须是单调时钟
    for name, pattern in [
        ("_last_new 写入", "self._last_new[scope] = time.monotonic()"),
        ("_last_new 读取", "time.monotonic() - self._last_new.get"),
        ("_last_ask 写入", "self._last_ask[scope] = time.monotonic()"),
        ("_last_ask 读取", "time.monotonic() - self._last_ask.get"),
    ]:
        check(name + "用单调时钟", pattern in src, pattern)

    # 冷却 <= 0 时要彻底跳过，不该还进分支
    check("new 冷却为 0 时跳过", 'if cooldown > 0:' in src)
    check("冷却不再直接用 time.time()",
          "cooldown - (time.time()" not in src,
          "还有残留的墙钟冷却计算")


def test_pitfall_memory(mod, tmp: Path) -> None:
    """坑点题出题记忆：出过的不能重复出，出完一轮才重置。

    没有这层记忆就是有放回抽样，64 条按生日问题算第 10 次左右就开始撞车。
    """
    print("\n[pitfalls] 出题记忆")
    store = mod.OjStore(tmp / "mem.db")
    scope = "group:1:grammar"

    eq("初始没有记录", store.used_pitfalls(scope), set())
    store.note_pitfall(scope, "a")
    store.note_pitfall(scope, "b")
    eq("记了 2 条", store.used_pitfalls(scope), {"a", "b"})

    store.note_pitfall(scope, "a")
    eq("重复记不叠加", len(store.used_pitfalls(scope)), 2)

    store.forget_pitfall(scope, "a")
    eq("单条遗忘", store.used_pitfalls(scope), {"b"})

    store.note_pitfall(scope, "a")
    store.reset_pitfalls(scope)
    eq("整轮重置", store.used_pitfalls(scope), set())

    # 不同群互不影响
    store.note_pitfall(scope, "x")
    eq("别的群不受影响", store.used_pitfalls("group:2:grammar"), set())
    store.close()


def test_pitfall_no_repeat(tmp: Path) -> None:
    """连续抽题不能重复，直到把这一类抽完。"""
    print("\n[pitfalls] 抽题不重复")
    import importlib
    import random as _random

    pf = importlib.import_module("sources.pitfalls")
    src = pf.PitfallSource()

    for cat in ["浮点数", "位运算", "输入输出"]:
        total = src.count(category=cat)
        if total == 0:
            check(f"{cat} 有题", False, "0 条")
            continue
        seen: set[str] = set()
        picked = []
        for _ in range(total):
            item = src.random_pitfall(exclude=tuple(seen), category=cat)
            seen.add(item["id"])
            picked.append(item["id"])
        check(f"{cat} 连抽 {total} 次不重复", len(set(picked)) == total, str(picked))
        # 抽完之后必须报错，而不是悄悄重复
        try:
            src.random_pitfall(exclude=tuple(seen), category=cat)
        except LookupError:
            check(f"{cat} 抽完后再抽要报错", True)
        else:
            check(f"{cat} 抽完后再抽要报错", False, "又抽出一条重复的")


def test_changelog(mod) -> None:
    """更新日志要能被 ask 读到，否则模型只会说「日志里没有」。"""
    print("\n[changelog] 更新日志")
    text = mod._load_changelog()
    check("能读到 CHANGELOG", bool(text), "文件没找到或为空")
    if not text:
        return

    check("有当前版本的节", f"## {mod.PLUGIN_VERSION}" in text,
          f"没找到 {mod.PLUGIN_VERSION}")

    section = mod._changelog_section(text, mod.PLUGIN_VERSION)
    check("能切出当前版本", bool(section) and mod.PLUGIN_VERSION in section)
    check("切出的节不含下一版", "## 0.5.5" not in section if mod.PLUGIN_VERSION != "0.5.5" else True)

    check("不存在的版本返回空", mod._changelog_section(text, "9.9.9") == "")

    # 关键词匹配：问更新相关的问题要能命中
    for q in ["你更新了什么功能", "0.5.5 改了什么", "版本号多少", "what's new"]:
        check(f"命中 {q!r}", bool(mod._CHANGELOG_ASK.search(q)))
    for q in ["今天天气", "这题怎么做", "rank 怎么用"]:
        check(f"不误伤 {q!r}", not mod._CHANGELOG_ASK.search(q))


def test_changelog_embedded(mod) -> None:
    """内嵌副本必须和 CHANGELOG.md 一致，且要能兜底。

    线上踩到的：数据卷里的 .md 没同步上，ask 就静默退回模型，
    模型答「快照里没有这个字段」——看起来像功能没做，其实是文件没找到。
    现在 .md 读不到就用内嵌的 .py 副本，两个副本靠测试保证不走样。
    """
    print("\n[changelog] 内嵌兜底")
    embedded = mod._EMBEDDED_CHANGELOG
    check("有内嵌副本", bool(embedded.strip()), "空的")

    import subprocess
    import sys
    from pathlib import Path as _P

    root = _P(__file__).resolve().parent.parent
    gen = root / "tools" / "sync_changelog.py"
    if gen.is_file():
        before = (root / "plugins" / "astrbot_plugin_kouhu_oj"
                  / "_changelog_text.py").read_text(encoding="utf-8")
        r = subprocess.run([sys.executable, str(gen)], capture_output=True, text=True)
        after = (root / "plugins" / "astrbot_plugin_kouhu_oj"
                 / "_changelog_text.py").read_text(encoding="utf-8")
        check("内嵌副本与 CHANGELOG.md 同步", before == after,
              "两者不一致，跑 python3 tools/sync_changelog.py 重新生成")

    check("内嵌副本有当前版本", f"## {mod.PLUGIN_VERSION}" in embedded,
          mod.PLUGIN_VERSION)

    # 兜底路径真的能切出版本节
    section = mod._changelog_section(embedded, mod.PLUGIN_VERSION)
    check("内嵌副本能切出版本节", bool(section) and mod.PLUGIN_VERSION in section)


def test_version_cmd(mod, tmp: Path) -> None:
    """ver 指令不依赖 ask 的意图匹配，必须稳定可用。"""
    print("\n[changelog] ver 指令")
    for q in ["ver", "version", "更新", "更新日志", "changelog", "/ver"]:
        check(f"{q!r} 命中 ver", bool(mod.RE_VER.match(q)))

    # 别把 ask 吃掉
    check("ask 不被 ver 抢", not mod.RE_VER.match("ask 更新了什么"))
    check("help 不被 ver 抢", not mod.RE_VER.match("help"))

    # 日志缺失时必须明说，不能静默。改完要还原，后面测试还要用。
    saved_paths = mod._CHANGELOG_CANDIDATES
    saved_embed = mod._EMBEDDED_CHANGELOG
    try:
        mod._CHANGELOG_CANDIDATES = (Path("/nonexistent/a.md"),)
        mod._EMBEDDED_CHANGELOG = ""
        text, origin = mod._changelog_source()
        check("缺失时返回空", text == "", text[:40])
        check("缺失时来源可诊断", "找不到" in origin, origin)
    finally:
        mod._CHANGELOG_CANDIDATES = saved_paths
        mod._EMBEDDED_CHANGELOG = saved_embed
    check("还原后能读到日志", bool(mod._load_changelog()))


def test_snapshot_healthy(mod, tmp: Path) -> None:
    """状态快照不能有「读取失败」这种含糊字段，且指令表必须是新名。

    这两个都是线上踩到的：配置项是坏值时 int() 抛异常被吞掉，
    模型只看到「读取失败」四个字，什么都查不出来；指令表还留着
    改名前的 problem/submit/grammar，模型照着旧名回答群友。
    """
    print("\n[snapshot] 状态快照")
    import asyncio

    class Ctx:
        async def get_current_chat_provider_id(self, umo=None):
            return "p"

    async def build(config):
        plug = mod.KouHuOJ(Ctx(), config)
        plug._store = mod.OjStore(tmp / f"snap{len(str(config))}.db")
        plug._started_at = __import__("time").monotonic()
        return plug._status_snapshot("group:1")

    # 正常配置
    snap = asyncio.run(build({"leaderboard_size": 10}))
    check("正常配置下积分榜可读", "读取失败" not in str(snap.get("本群积分榜")),
          str(snap.get("本群积分榜")))

    # 坏配置：None / 字符串垃圾，都不能炸
    for bad in (None, "abc", "", []):
        try:
            snap = asyncio.run(build({"leaderboard_size": bad}))
            ok = "读取失败" not in str(snap.get("本群积分榜"))
            check(f"坏配置 {bad!r} 不炸", ok, str(snap.get("本群积分榜")))
        except Exception as exc:  # noqa: BLE001
            check(f"坏配置 {bad!r} 不炸", False, f"{type(exc).__name__}: {exc}")

    # 指令表必须是改名后的
    snap = asyncio.run(build({"leaderboard_size": 10}))
    cmds = " ".join(snap.get("指令", {}).keys())
    check("指令表用新名 cur", "cur" in cmds, cmds)
    check("指令表用新名 sub", "sub" in cmds, cmds)
    check("指令表用新名 grm", "grm" in cmds, cmds)
    stale = [w for w in ("problem", "submit", "grammar") if w in cmds]
    check("指令表没有旧名残留", not stale, str(stale))
    check("快照带更新日志", any("更新日志" in k for k in snap), str(list(snap.keys())))


def test_verified_flag() -> None:
    """题库里「机器证实过」和「还没补骨架」要能区分开。

    不做这个区分的话，一条只是片段、从没被跑过的题，和一条真跑通过、
    buggy 确实报错 fix 确实修好的题，在数据里长得一模一样，
    后面接手的人不知道该信谁。
    """
    print("\n[pitfalls] 验证状态可区分")
    import json
    from pathlib import Path as _P

    items = json.loads(
        (_P(__file__).resolve().parent.parent
         / "plugins" / "astrbot_plugin_kouhu_oj" / "pitfalls.json").read_text(encoding="utf-8")
    )
    missing = [i["id"] for i in items if "verified" not in i]
    check("每条都有 verified 字段", not missing, str(missing[:5]))

    proven = [i for i in items if i.get("verified")]
    check("正式条目全部有行为验证标记", len(proven) == len(items) and bool(proven), f"{len(proven)} 条")

    # 证实的条目必须真带 case，否则这个标记就是空的
    fake = [i["id"] for i in proven if not i.get("case")]
    check("标记为证实的必须带 case", not fake, str(fake[:5]))

    # 带 case 的条目要真的写了 buggy/fix 两组期望，且不同
    bad = []
    for i in proven:
        c = i["case"]
        b = (c.get("buggy") or {}).get("value")
        f = (c.get("fix") or {}).get("value")
        if b is None or f is None:
            bad.append(f"{i['id']}: 缺期望值")
        elif b == f:
            bad.append(f"{i['id']}: buggy 和 fix 期望相同，证明不了有 bug")
    check("证实的条目两侧期望不同", not bad, "; ".join(bad[:3]))


def test_category_summary() -> None:
    """grm list 的概览要可读，不能是一串 L1122233。"""
    print("\n[pitfalls] 分类概览")
    import importlib
    src = importlib.import_module("sources.pitfalls").PitfallSource()
    text = src.category_summary()
    check("概览有总数", "共" in text, text[:80])
    check("概览用可读难度名", "入门" in text and "常见" in text, text[:120])
    check("概览没有 L1122233 这种密文", "L1" not in text and "L2" not in text, text[:120])
    check("概览带抽题指引", "grm new" in text, text[-120:])


def test_no_title_leak(tmp: Path) -> None:
    """题面卡片不能出现题目名字、题号、难度、分值。

    这是「防搜题解」的核心：有题号就能直接去搜，有难度分值会直接劝退。
    """
    print("\n[render] 题面卡片不泄底")
    import importlib

    render = importlib.import_module("render")

    prob = {
        "cf_key": "1542E2",
        "title": "E2. Abnormal Permutation Pairs (hard version)",
        "tier": 2600,
        "points": 1950,
        "statement_cn": "给定 $$$n$$$，求方案数。",
        "examples": [{"input": "1", "output": "1"}],
        "time_limit": 4.0,
        "memory_limit": 512,
    }

    # 直接检查卡片头部，避免依赖 OCR
    import inspect
    src = inspect.getsource(render.render_cf_statement)
    check("题面卡头部不含 title", 'problem.get("title")' not in src, src[:200])
    check("题面卡头部不含分值", "points" not in src, src[:200])
    check("题面卡头部不含难度", "tier" not in src, src[:200])

    # 文件名里也不能带题号
    path = render.render_cf_statement(prob, out_dir=str(tmp / "img"), width=600)
    name = Path(path).name
    check("文件名不含题号", "1542" not in name and "E2" not in name, name)

    # 坑点题卡片：标题留空（坑点的 title 本身就是答案）
    pit = render.render_problem(
        {"title": "", "content_html": "<p>这段代码结果不对</p>", "tags": [], "link": ""},
        out_dir=str(tmp / "img"), width=600,
    )
    check("坑点卡能渲染", Path(pit).exists())


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
        eq("包加载下 main 可导入", main_mod.PLUGIN_VERSION, "0.21.7")
        cmds = [n for n in dir(main_mod.KouHuOJ) if n.startswith("cmd_")]
        check(f"指令齐全（{len(cmds)} 个）", len(cmds) >= 12, str(sorted(cmds)))
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
    test_pitfalls(tmp)
    test_no_title_leak(tmp)
    test_pitfall_memory(mod, tmp)
    test_pitfall_no_repeat(tmp)
    test_category_summary()
    test_verified_flag()
    test_changelog(mod)
    test_snapshot_healthy(mod, tmp)
    test_changelog_embedded(mod)
    test_version_cmd(mod, tmp)
    test_clock_rollback(tmp)
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
