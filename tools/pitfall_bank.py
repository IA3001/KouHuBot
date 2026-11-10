#!/usr/bin/env python3
"""Maintain an external JSON-list pitfall bank without changing the bundled bank.

Examples (all paths are explicit; candidate files are JSON lists):
  python tools/pitfall_bank.py check --bank /tmp/bank.json
  python tools/pitfall_bank.py migrate --bank /tmp/legacy.json --output /tmp/bank.json
  python tools/pitfall_bank.py report --bank /tmp/bank.json --output /tmp/report.json
  python tools/pitfall_bank.py stage --bank /tmp/bank.json --candidates /tmp/inbox.json --id example --output /tmp/staged.json
  python tools/pitfall_bank.py approve --candidates /tmp/staged.json --id example --reviewer Alice --note 'reproduced' --bank /tmp/bank.json --archive /tmp/history
  python tools/pitfall_bank.py rollback --archive /tmp/history/bank-SHA256.json --bank /tmp/bank.json

Migration preserves IDs and content; missing provenance stays honestly unknown.
Approval executes candidate C++ through the repository validator: use trusted local
candidates only. A verified flag or an uploaded evidence file cannot grant approval.
Successful approval stores fresh validator evidence and review history, snapshots
exact old bytes by SHA-256, and atomically replaces the destination. Rollback checks
list/schema validity and can snapshot the displaced bank with --archive-dir.

Public APIs: load_bank(path), check_bank(items), enrich_entry(item),
report_bank(items), validate_one(item), stage(bank, candidates, entry_id),
approve(bank_path, candidates_path, entry_id, reviewer, note, archive_dir),
rollback(snapshot, bank_path, archive_dir=None), main(argv=None).
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlparse

REQUIRED = ("id", "category", "title", "question", "buggy_code", "answer", "fix")
UNKNOWN_SOURCE = {"url": None, "collected_at": None, "license": "unknown",
                  "rewrite": "legacy repository entry; upstream unknown"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def load_bank(path):
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise ValueError("bank/candidates must be a JSON list of objects")
    return items


def _normalized(code):
    # Preserve literals and token boundaries; ignore formatting and comments.
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/|\w+|[^\s]', code)
    return tuple(t for t in tokens if not t.startswith(("//", "/*")))


def check_bank(items):
    """Return human-readable errors; legacy metadata is optional until migration."""
    errors, ids, codes = [], set(), {}
    if not isinstance(items, list):
        return ["bank must be a JSON list"]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"entry {index}: must be an object")
            continue
        label = item.get("id", index)
        for key in REQUIRED:
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"{label}: {key} must be a nonempty string")
        pid = item.get("id")
        if isinstance(pid, str):
            if pid in ids:
                errors.append(f"{label}: duplicate id")
            ids.add(pid)
        code = item.get("buggy_code")
        if isinstance(code, str) and code.strip():
            normalized = _normalized(code)
            if normalized in codes:
                errors.append(f"{label}: duplicate buggy_code with {codes[normalized]}")
            codes[normalized] = label
        question, answer = item.get("question"), item.get("answer")
        # Only whole, substantial answers: shared keywords/snippets are legitimate.
        if isinstance(question, str) and isinstance(answer, str) and len(answer.strip()) >= 20 and answer.strip() in question:
            errors.append(f"{label}: question contains the complete answer")
        fix = item.get("fix")
        if isinstance(question, str) and isinstance(fix, str) and len(fix.strip()) >= 20:
            blocks = re.findall(r"```[^\n]*\n(.*?)```", question, re.S)
            if any(_normalized(block) == _normalized(fix) for block in blocks):
                errors.append(f"{label}: question code block contains the complete fix")
        if "revision" in item and (type(item["revision"]) is not int or item["revision"] < 1):
            errors.append(f"{label}: revision must be a positive integer")
        if "verified" in item and type(item["verified"]) is not bool:
            errors.append(f"{label}: verified must be boolean")
        if "source" in item and not isinstance(item["source"], (str, dict)):
            errors.append(f"{label}: source must be a legacy URL or provenance object")
        if "review" in item and not isinstance(item["review"], dict):
            errors.append(f"{label}: review must be an object")
        if "review_history" in item and (not isinstance(item["review_history"], list)
                                         or any(not isinstance(r, dict) for r in item["review_history"])):
            errors.append(f"{label}: review_history must be a list of objects")
        if "case" in item and not isinstance(item["case"], dict):
            errors.append(f"{label}: case must be an object")
    return errors


def _require_valid(items):
    errors = check_bank(items)
    if errors:
        raise ValueError("; ".join(errors))


def enrich_entry(item):
    result = deepcopy(item)
    source = result.get("source")
    if isinstance(source, dict):
        result["source"] = {**UNKNOWN_SOURCE, **source}
    else:
        result["source"] = dict(UNKNOWN_SOURCE)
        if isinstance(source, str) and source.strip():
            result["source"]["url"] = source
            result["source"]["rewrite"] = "legacy repository entry; attribution and license unverified"
    result.setdefault("revision", 1)
    result.setdefault("review", {"status": "pending"})
    if result.get("verified") is not True or not result.get("case"):
        result["review"] = {**result["review"], "status": "pending"}
    return result


def _source_known(item):
    source = item.get("source")
    if not isinstance(source, dict):
        return False
    url, license_name = source.get("url"), source.get("license")
    if not isinstance(url, str) or not isinstance(license_name, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("https", "http") and bool(parsed.netloc) and license_name.strip().lower() not in ("", "unknown", "unspecified", "n/a", "none")


def report_bank(items):
    entries = [enrich_entry(i) for i in items]
    return {"total": len(items), "errors": check_bank(items),
            "categories": dict(sorted(Counter(i.get("category", "unknown") for i in entries).items())),
            "verification": {"verified_with_case": sum(i.get("verified") is True and bool(i.get("case")) for i in entries),
                             "pending": sum(i.get("verified") is not True or not i.get("case") for i in entries)},
            "review_statuses": dict(Counter(i["review"].get("status", "pending") for i in entries)),
            "source_status": {"known": sum(_source_known(i) for i in entries),
                              "unknown": sum(not _source_known(i) for i in entries)},
            "revisions": {i["id"]: i["revision"] for i in entries}}


def _atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _write_json(path, value):
    _atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _snapshot(raw, archive_dir):
    digest = hashlib.sha256(raw).hexdigest()
    path = Path(archive_dir) / f"bank-{digest}.json"
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("archive hash collision or corrupt snapshot")
    else:
        _atomic_bytes(path, raw)
    return path


def _select(items, entry_id):
    matches = [i for i in items if i.get("id") == entry_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one candidate with id {entry_id!r}")
    return matches[0]


def stage(bank, candidates, entry_id):
    _require_valid(bank)
    _require_valid(candidates)
    candidate = enrich_entry(_select(candidates, entry_id))
    existing = next((i for i in bank if i["id"] == entry_id), None)
    candidate["revision"] = existing.get("revision", 1) + 1 if existing else 1
    candidate["review"] = {"status": "staged", "staged_at": _now()}
    _require_valid([i for i in bank if i["id"] != entry_id] + [candidate])
    return [candidate]


def validate_one(item):
    """Run fresh repository validation; return its passed/failed/skipped result."""
    path = Path(__file__).with_name("validate_pitfalls.py")
    spec = importlib.util.spec_from_file_location("pitfall_bank_validator", path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    # The validator uses IDs in executable filenames; external IDs stay stable
    # in the bank but must never become paths outside its temporary directory.
    safe_item = deepcopy(item)
    safe_item["id"] = "entry_" + hashlib.sha256(item["id"].encode("utf-8")).hexdigest()
    with tempfile.TemporaryDirectory(prefix="pitfall_bank_validate_") as directory:
        validate = getattr(validator, "validate_one", None) or validator.validate_item
        result = validate(safe_item, Path(directory))
    result["id"] = item["id"]
    return result


def _require_evidence(item, evidence):
    passed = evidence.get("passed", [])
    behavior = all(any(p.startswith(f"L3-{side}") for p in passed) for side in ("buggy", "fix"))
    sanitized = all(any(p.startswith(f"L4-{side}") for p in passed) for side in ("buggy", "fix"))
    if evidence.get("failed") or not (behavior or sanitized):
        raise ValueError("runtime validation failed or lacks both buggy and fix L3/L4 evidence")
    if item["case"]["buggy"].get("kind") == "ub" and not sanitized:
        raise ValueError("UB case requires successful L4 evidence for buggy and fix")


def approve(bank_path, candidates_path, entry_id, reviewer, note, archive_dir):
    bank_path = Path(bank_path)
    raw = bank_path.read_bytes()
    bank, candidates = load_bank(bank_path), load_bank(candidates_path)
    staged = stage(bank, candidates, entry_id)[0]
    if not reviewer.strip() or not note.strip():
        raise ValueError("reviewer and review note must be nonempty")
    if staged.get("verified") is not True or not isinstance(staged.get("case"), dict):
        raise ValueError("approval requires verified=true and a runtime case")
    case = staged["case"]
    if not all(isinstance(case.get(side), dict) and case[side].get("kind") for side in ("buggy", "fix")):
        raise ValueError("case requires buggy and fix expectations")
    if case["buggy"] == case["fix"]:
        raise ValueError("buggy and fix expectations must distinguish behavior")
    for side in ("buggy", "fix"):
        if case[side].get("kind") == "stdout" and not str(case[side].get("value", "")).strip():
            raise ValueError("stdout expectation must be nonempty")
    if not _source_known(staged):
        raise ValueError("approval requires provenance URL and known license")
    evidence = validate_one(staged)
    _require_evidence(staged, evidence)
    old = next((i for i in bank if i["id"] == entry_id), None)
    history = deepcopy(old.get("review_history", [])) if old else []
    review = {"status": "approved", "reviewer": reviewer, "note": note,
              "reviewed_at": _now(), "revision": staged["revision"]}
    history.append(review)
    staged.update(review=review, review_history=history, validation=evidence)
    updated = [staged if i["id"] == entry_id else i for i in bank]
    if old is None:
        updated.append(staged)
    _require_valid(updated)
    if bank_path.read_bytes() != raw:
        raise ValueError("bank changed during validation; retry against the current bank")
    snapshot = _snapshot(raw, archive_dir)
    _write_json(bank_path, updated)
    return {"id": entry_id, "revision": staged["revision"], "archive": str(snapshot)}


def rollback(snapshot, bank_path, archive_dir=None):
    raw = Path(snapshot).read_bytes()
    restored = load_bank(snapshot)
    _require_valid(restored)
    bank_path = Path(bank_path)
    if archive_dir is not None and bank_path.exists():
        _snapshot(bank_path.read_bytes(), archive_dir)
    _atomic_bytes(bank_path, raw)
    return {"restored": str(snapshot), "total": len(restored)}


def _separate(output, *inputs):
    if any(Path(output).resolve() == Path(source).resolve() for source in inputs):
        raise ValueError("output must differ from input files")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "migrate", "report", "stage", "approve", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--bank", required=True, type=Path)
        if name in ("migrate", "report", "stage"):
            command.add_argument("--output", required=True, type=Path)
        if name in ("stage", "approve"):
            command.add_argument("--candidates", required=True, type=Path)
            command.add_argument("--id", required=True)
        if name == "approve":
            command.add_argument("--reviewer", required=True)
            command.add_argument("--note", required=True)
            command.add_argument("--archive", required=True, type=Path)
        if name == "rollback":
            command.add_argument("--archive", required=True, type=Path, help="snapshot JSON file")
            command.add_argument("--archive-dir", type=Path, help="optionally snapshot the displaced bank")
    args = parser.parse_args(argv)
    try:
        if args.command == "approve":
            result = approve(args.bank, args.candidates, args.id, args.reviewer, args.note, args.archive)
        elif args.command == "rollback":
            result = rollback(args.archive, args.bank, args.archive_dir)
        else:
            items = load_bank(args.bank)
            _require_valid(items)
            if args.command == "check":
                result = {"valid": True, "total": len(items)}
            else:
                _separate(args.output, args.bank, *([args.candidates] if args.command == "stage" else []))
                if args.command == "stage":
                    result = stage(items, load_bank(args.candidates), args.id)
                elif args.command == "migrate":
                    result = [enrich_entry(i) for i in items]
                else:
                    result = report_bank(items)
                _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(f"pitfall_bank: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
