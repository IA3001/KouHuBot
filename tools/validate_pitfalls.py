#!/usr/bin/env python3
"""坑点题库验证器 —— 每条题必须「能被证明是错的」才能进库。

为什么需要这个：
    坑点题的 `answer` 是直接给群友看的，判题也是拿它比对。一条错题比没有题
    更糟——答案错，判题会把对的分析判成 WA；代码其实没错，玩家怎么找也找
    不到 bug，只会以为 bot 坏了。靠人眼审几百条是不现实的，只能靠真跑一遍。

四级检查，逐级变严：
    L1 编译    buggy_code 和 fix 加壳后能编过（语法/类型层面自洽）
    L2 警告    buggy 触发 -Wall 的特定警告，fix 不触发
    L3 行为    buggy 跑出错误结果 / 崩溃，fix 跑对
    L4 UB      -fsanitize=undefined,address 在 buggy 上命中，fix 上干净

一条题用哪几级，看它有没有 `case` 字段：

    "case": {
      "prelude":  "额外声明",          # 可选，塞在 main 前面
      "run":      "塞进 main 的语句",   # 必填
      "stdin":    "喂给程序的输入",      # 可选
      "buggy":    {"kind": "warn|crash|stdout|ub", "value": "..."},
      "fix":      {"kind": "stdout", "value": "期望输出"}
    }

没有 `case` 的条目只跑 L1（编译），并在报告里标成未验证——
扩库时这些条目要优先补 case。

用法：
    python tools/validate_pitfalls.py                # 全部
    python tools/validate_pitfalls.py --only 二分    # 只看某一类
    python tools/validate_pitfalls.py --strict       # 有未验证条目就非零退出
    python tools/validate_pitfalls.py --fix-cases    # 自动给简单条目生成 case
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "plugins" / "astrbot_plugin_kouhu_oj" / "pitfalls.json"

CXX = os.getenv("KOUHU_CXX", "g++")
WARN_FLAGS = ["-Wall", "-Wextra", "-Wno-unused-variable", "-Wno-unused-parameter",
              "-Wno-unused-but-set-variable", "-Wno-sign-compare"]
BASE_FLAGS = ["-std=c++17", "-O2", *WARN_FLAGS]
SAN_FLAGS = ["-std=c++17", "-O1", "-g",
             "-fsanitize=undefined,address",
             "-fno-sanitize-recover=all"]
# 编译超时：正常题目 <2s，卡住说明代码本身有问题
COMPILE_TIMEOUT = 30
RUN_TIMEOUT = 5

INCLUDES = "#include <bits/stdc++.h>\nusing namespace std;\n"


# ----------------------------------------------------------------------
# 编译 & 运行
# ----------------------------------------------------------------------
def _first_error(log: str) -> str:
    """从编译器输出里挑出第一行真正的 error/未声明标识符。"""
    for line in log.splitlines():
        if "error:" in line:
            return line.strip()
    # 没有 error 但有 undefined reference：说明是片段，缺声明
    unk = re.findall(r"error|‘(\w+)’ was not declared", log)
    for line in log.splitlines():
        if "was not declared" in line or "undefined reference" in line:
            return line.strip()
    tail = [l for l in log.strip().splitlines() if l.strip()]
    return tail[-1].strip() if tail else "未知错误"


def _compile(source: str, tmp: Path, *, sanitize: bool, name: str) -> tuple[bool, str]:
    """编译，返回 (是否成功, 编译器输出)。"""
    src = tmp / f"{name}.cpp"
    exe = tmp / name
    src.write_text(source, encoding="utf-8")
    flags = SAN_FLAGS if sanitize else BASE_FLAGS
    try:
        proc = subprocess.run(
            [CXX, *flags, "-o", str(exe), str(src)],
            capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "编译超时"
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def _run(exe: Path, stdin: str) -> tuple[int, str, str]:
    """跑程序，返回 (退出码, stdout, stderr)。崩溃/超时用特殊退出码。"""
    try:
        proc = subprocess.run(
            [str(exe)], input=stdin, capture_output=True, text=True,
            timeout=RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT"
    return proc.returncode, proc.stdout, proc.stderr


def _build_program(item: dict, *, fixed: bool, where: str) -> str:
    """把片段拼成完整程序。

    where="file"：片段是文件作用域的声明/函数，driver 在 main 里调用它
    where="main"：片段本身就是 main 里的语句，直接铺进去

    坑点题都是片段，作者不会（也不该）每次都标注它属于哪种，所以调用方
    两种都试一遍，能编过的那个就是对的。
    """
    case = item.get("case") or {}
    body = item.get("fix") if fixed else item.get("buggy_code")
    prelude = case.get("prelude") or ""
    driver = case.get("run") or ""

    if where == "main":
        inner = f"{body}\n{driver}"
        return (
            f"{INCLUDES}\n{prelude}\n"
            f"int main() {{\n{inner}\n    return 0;\n}}\n"
        )
    return (
        f"{INCLUDES}\n{prelude}\n"
        f"{body}\n"
        f"int main() {{\n{driver}\n    return 0;\n}}\n"
    )


def _compile_both(item: dict, *, fixed: bool, tmp: Path, name: str,
                  sanitize: bool = False) -> tuple[bool, str, str]:
    """两种位置都试，返回 (是否成功, 编译器输出, 成功的位置)。"""
    last_log = ""
    for where in ("file", "main"):
        ok, log = _compile(_build_program(item, fixed=fixed, where=where), tmp,
                           sanitize=sanitize, name=name)
        if ok:
            return True, log, where
        last_log = log
    return False, last_log, ""


# ----------------------------------------------------------------------
# 判定
# ----------------------------------------------------------------------
def _matches(actual: tuple[int, str, str], expect: dict) -> bool:
    kind = expect.get("kind")
    value = expect.get("value") or ""
    code, out, err = actual

    if kind == "crash":
        return code != 0 and code != -9 and "TIMEOUT" not in err
    if kind == "timeout":
        return code == -9 or "TIMEOUT" in err
    if kind == "stdout":
        return value.strip() in out
    if kind == "stderr":
        return value in err
    if kind == "ub":
        # sanitizer 报的错必须出现在 stderr 里
        return "runtime error" in err.lower() or "Sanitizer" in err
    return False


def validate_item(item: dict, tmp: Path) -> dict:
    """跑一条题的全部可用检查，返回结果。"""
    pid = item["id"]
    result = {"id": pid, "category": item.get("category"), "level": item.get("level"),
              "passed": [], "failed": [], "skipped": []}
    case = item.get("case") or {}

    def ok(name: str) -> None:
        result["passed"].append(name)

    def bad(name: str, why: str) -> None:
        result["failed"].append(f"{name}: {why}")

    def skip(name: str, why: str) -> None:
        result["skipped"].append(f"{name}: {why}")

    # ---- L1：两边都要能编过（位置自动判定）----
    ok_f, log_f, where_f = _compile_both(item, fixed=True, tmp=tmp, name=f"{pid}_f")
    if not ok_f:
        bad("L1-fix编译", _first_error(log_f))
        return result
    ok("L1-fix编译")

    ok_b, log_b, where_b = _compile_both(item, fixed=False, tmp=tmp, name=f"{pid}_b")
    if not ok_b:
        bad("L1-buggy编译", _first_error(log_b))
        return result
    ok("L1-buggy编译")
    result["where"] = where_b

    if not case:
        result["skipped"].append("没有 case，只验证了编译")
        return result

    # ---- L2：警告 ----
    expect_b = case.get("buggy") or {}
    if expect_b.get("kind") == "warn":
        want = expect_b.get("value") or ""
        if want and want not in log_b:
            bad("L2-buggy警告", f"没出现 {want!r}")
        else:
            ok("L2-buggy警告")
        if want and want in log_f:
            bad("L2-fix警告", f"fix 里仍然出现 {want!r}")
        else:
            ok("L2-fix无此警告")

    # ---- L3：行为 ----
    stdin = case.get("stdin") or ""
    expect_f = case.get("fix")
    if expect_f:
        code_f, out_f, err_f = _run(tmp / f"{pid}_f", stdin)
        if _matches((code_f, out_f, err_f), expect_f):
            ok("L3-fix行为")
        else:
            bad("L3-fix行为", f"期望 {expect_f} 实得 code={code_f} out={out_f!r}")

    if expect_b:
        code_b, out_b, err_b = _run(tmp / f"{pid}_b", stdin)
        if _matches((code_b, out_b, err_b), expect_b):
            ok("L3-buggy行为")
        else:
            bad("L3-buggy行为", f"期望 {expect_b} 实得 code={code_b} out={out_b!r}")

    # ---- L4：sanitizer ----
    if expect_b.get("kind") == "ub":
        ok_sb, log_sb, _ = _compile_both(item, fixed=False, tmp=tmp, name=f"{pid}_bs",
                                         sanitize=True)
        ok_sf, log_sf, _ = _compile_both(item, fixed=True, tmp=tmp, name=f"{pid}_fs",
                                         sanitize=True)
        if not ok_sb:
            skip("L4", "buggy 加 sanitizer 编不过")
        elif not ok_sf:
            skip("L4", "fix 加 sanitizer 编不过")
        else:
            code_bs, out_bs, err_bs = _run(tmp / f"{pid}_bs", stdin)
            code_fs, out_fs, err_fs = _run(tmp / f"{pid}_fs", stdin)
            if _matches((code_bs, out_bs, err_bs), {"kind": "ub"}):
                ok("L4-buggy命中UB")
            else:
                bad("L4-buggy命中UB", f"sanitizer 没报错，code={code_bs}")
            if _matches((code_fs, out_fs, err_fs), {"kind": "ub"}):
                bad("L4-fix干净", f"fix 也触发了 sanitizer：{err_fs[:200]}")
            else:
                ok("L4-fix干净")

    return result


# ----------------------------------------------------------------------
# 自动生成 case（只处理最稳的一类：整段就是 main 里的语句）
# ----------------------------------------------------------------------
def guess_case(item: dict) -> dict | None:
    """给「整段就是语句」的条目补一个最简 case。

    只做保守推断：代码里没有函数定义、没有 main，就当它是 main 里的语句。
    推不出来就返回 None——宁可不生成，也不要生成一个假验证。
    """
    code = item.get("buggy_code") or ""
    if re.search(r"\b(int|void|double|bool|char|long)\s+\w+\s*\([^)]*\)\s*\{", code):
        return None       # 有函数定义，无法自动当语句体
    if "main" in code:
        return None
    return {"run": code, "buggy": {"kind": "crash"}, "fix": {"kind": "stdout", "value": ""}}


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只验证某一类")
    ap.add_argument("--strict", action="store_true", help="有未验证/失败条目就非零退出")
    ap.add_argument("--fix-cases", action="store_true", help="给简单条目自动补 case 并写回")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--mark", action="store_true",
                    help="把验证结果写回 pitfalls.json 的 verified 字段")
    args = ap.parse_args()

    if not Path(CXX).exists() and not subprocess.run(["which", CXX],
                                                     capture_output=True).returncode == 0:
        print(f"找不到编译器 {CXX}，验证器需要 g++", file=sys.stderr)
        return 2

    all_items = json.loads(BANK.read_text(encoding="utf-8"))
    items = list(all_items)
    if args.only:
        items = [i for i in items if i.get("category") == args.only]
        if not items:
            print(f"没有分类 {args.only}", file=sys.stderr)
            return 2

    if args.fix_cases:
        added = 0
        for item in all_items:
            if item.get("case"):
                continue
            case = guess_case(item)
            if case:
                item["case"] = case
                added += 1
        BANK.write_text(json.dumps(all_items, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"自动补了 {added} 条 case（保守推断，需要人工复核）")
        return 0

    results = []
    with tempfile.TemporaryDirectory(prefix="kouhu_val_") as td:
        tmp = Path(td)
        for item in items:
            results.append(validate_item(item, tmp))

    n_pass = sum(1 for r in results if not r["failed"])
    n_fail = len(results) - n_pass
    n_unverified = sum(1 for r in results if r.get("needs_prelude")
                       or any("没有 case" in s for s in r["skipped"]))

    if args.mark:
        # 把「机器证实过」和「只是还没补骨架」分开记。
        # 不加这个字段的话，一条片段题和一条真跑通的题看起来一样，
        # 后面接手的人不知道该信谁。
        by_id = {r["id"]: r for r in results}
        changed = 0
        for item in all_items:
            r = by_id.get(item["id"])
            if not r:
                continue
            # 「能编译」不等于「证实了有 bug」。只有跑过行为检查
            # （L3 输出对得上、或 L4 sanitizer 命中）才算真的证实。
            behavioral = any(
                x.startswith("L3") or x.startswith("L4") for x in r["passed"]
            )
            want = (not r["failed"]) and behavioral
            if item.get("verified") != want:
                item["verified"] = want
                changed += 1
        BANK.write_text(json.dumps(all_items, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        n_true = sum(1 for i in all_items if i.get("verified"))
        print(f"已写回 verified 字段（改动 {changed} 条）："
              f"{n_true} 条机器证实 / {len(all_items) - n_true} 条待验证")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r["failed"]:
                print(f"❌ {r['id']}  [{r['category']} L{r['level']}]")
                for f in r["failed"]:
                    print(f"     {f}")
            elif args.strict and (r.get("needs_prelude")
                                  or any("没有 case" in s for s in r["skipped"])):
                why = (r["skipped"][0] if r["skipped"] else "").split("：")[0]
                print(f"⚪ {r['id']}  [{r['category']} L{r['level']}] {why}")

        print(f"\n{'─' * 60}")
        print(f"共 {len(results)} 条：✅ {n_pass} 通过 / ❌ {n_fail} 失败 / "
              f"⚪ {n_unverified} 只验到编译")

    if n_fail:
        return 1
    if args.strict and n_unverified:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
