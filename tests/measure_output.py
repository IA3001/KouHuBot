"""Measure per-command output size so UX bloat is quantified, not guessed."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _astrbot_stub as stub
import local_harness as h


async def main() -> None:
    data_root = Path(tempfile.mkdtemp(prefix="kouhu_measure_"))
    mod = stub.load_plugin(data_root=data_root)

    async def fake_editorial(contest_id, **kwargs):
        return {'url': f'https://codeforces.com/blog/entry/{contest_id}',
                'title': 'Tutorial',
                'text': 'Official tutorial fixture: derive a linear solution with prefix sums.'}

    mod.fetch_official_editorial = fake_editorial
    import scoring
    ctx = h.FakeContext()
    plugin = mod.KouHuOJ(ctx, {"judge_provider_id": "mock-provider",
                               "new_cooldown_seconds": 0, "ask_cooldown_seconds": 0,
                               "judge_ack_delay_seconds": 0.01, "judge_timeout_seconds": 30,
                               "image_width": 900})
    await plugin.initialize()
    for p in h.FAKE_PROBLEMS:
        entry = dict(p)
        entry["tier"] = scoring.tier_of(p["rating"])
        entry["points"] = scoring.points_for(entry["tier"])
        plugin._cache.save(entry)
    fake = h.FakeCfSource(h.FAKE_PROBLEMS)
    for p in h.FAKE_PROBLEMS:
        p["tier"] = scoring.tier_of(p["rating"])
        p["points"] = scoring.points_for(p["tier"])
    plugin._cf = fake

    group = "556677889"
    E = stub.AstrMessageEvent
    alice = {"group_id": group, "sender_id": "1001", "sender_name": "Alice"}
    cases = [
        ("帮助 help", "help", {}),
        ("总览 status", "status", {}),
        ("new", "new", {}),
        ("cur", "problem", {}),
        ("hint 无参", "hint", {}),
        ("rank", "rank", {}),
        ("history", "history", {}),
        ("grm 总览", "pitfall", {}),
        ("ping", "ping", {}),
        ("sub 空", "submit", {}),
        ("me", "me", {}),
        ("rules", "rules", {}),
        ("about", "about", {}),
        ("version", "version", {}),
        ("new tags", "new", {}),
    ]
    print(f"{'指令':<18}{'条数':>4}{'字符':>7}{'最长一条':>9}")
    for label, handler, extra in cases:
        text = {"new tags": "new tags"}.get(label, label.split()[0])
        event = E(text, **alice)
        try:
            outs = await h.drive(plugin, "cmd_" + handler, event)
        except Exception as exc:
            print(f"{label:<18} ERR {type(exc).__name__}: {exc}")
            continue
        total = sum(len(o) for o in outs)
        longest = max((len(o) for o in outs), default=0)
        print(f"{label:<18}{len(outs):>4}{total:>7}{longest:>9}")
    await plugin.terminate()


if __name__ == "__main__":
    asyncio.run(main())
