"""Maintenance CLI tests: python -m unittest discover -s tests -p test_pitfall_bank.py."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "pitfall_bank", Path(__file__).resolve().parents[1] / "tools" / "pitfall_bank.py")
bank = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bank)


def entry(**changes):
    result = {"id": "integer-example", "category": "integers", "title": "Wrong increment",
              "question": "Why does this output the wrong number?", "buggy_code": "int x = 1;",
              "answer": "The initial value must be two.", "fix": "int x = 2;",
              "verified": True, "case": {"run": "cout << x;",
                  "buggy": {"kind": "stdout", "value": "1"},
                  "fix": {"kind": "stdout", "value": "2"}},
              "source": {"url": "https://example.org/problem", "license": "CC0-1.0",
                         "collected_at": "2026-11-10", "rewrite": "minimal reproduction"}}
    result.update(changes)
    return result


def evidence():
    return {"passed": ["L1-fix编译", "L1-buggy编译", "L3-fix行为", "L3-buggy行为"],
            "failed": [], "skipped": []}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bank_path = self.root / "bank.json"
        self.candidates = self.root / "candidates.json"
        self.archive = self.root / "history"
        self.write(self.bank_path, [entry(verified=False)])
        self.write(self.candidates, [entry()])

    def write(self, path, items):
        path.write_text(json.dumps(items, ensure_ascii=False) + "\n", encoding="utf-8")

    def cli(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return bank.main(list(map(str, args)))

    def approve(self):
        return bank.approve(self.bank_path, self.candidates, "integer-example", "Alice", "reproduced", self.archive)

    def test_migration_preserves_id_and_admits_unknown_provenance(self):
        legacy = entry()
        del legacy["source"]
        legacy.pop("case")
        migrated = bank.enrich_entry(legacy)
        self.assertEqual(migrated["id"], legacy["id"])
        self.assertEqual(migrated["source"], bank.UNKNOWN_SOURCE)
        self.assertEqual(migrated["revision"], 1)
        self.assertEqual(migrated["review"]["status"], "pending")
        self.assertNotIn("source", legacy)
        url = bank.enrich_entry(entry(source="https://example.org/legacy"))
        self.assertEqual(url["source"]["url"], "https://example.org/legacy")
        self.assertEqual(url["source"]["license"], "unknown")

    def test_check_required_fields_ids_and_normalized_duplicate_code(self):
        other = entry(id="other", buggy_code="int  x=1; // same code\n")
        errors = bank.check_bank([entry(), other])
        self.assertTrue(any("duplicate buggy_code" in error for error in errors))
        self.assertTrue(any("duplicate id" in error for error in bank.check_bank([entry(), entry()])))
        self.assertTrue(any("answer" in error for error in bank.check_bank([entry(answer="")])) )
        self.assertTrue(bank.check_bank([entry(revision=True)]))
        self.assertEqual(bank.check_bank([entry(), entry(id="other", buggy_code='cout << "a b";')]), [])
        self.assertEqual(bank._normalized('cout << "a b";'), bank._normalized('cout<<"a b";'))
        self.assertNotEqual(bank._normalized('cout << "a b";'), bank._normalized('cout << "ab";'))

    def test_question_leaks_only_complete_answer_or_fix_block(self):
        self.assertTrue(bank.check_bank([entry(question=entry()["answer"])]))
        fixed = "int initial_value = 123456;"
        self.assertTrue(bank.check_bank([entry(fix=fixed, question=f"What happens?\n```cpp\n{fixed}\n```")] ))
        self.assertEqual(bank.check_bank([entry(question="Consider x and the initial value.")]), [])

    def test_report_counts_coverage_status_sources_revisions(self):
        items = [entry(), entry(id="unknown", verified=False, source=None, revision=3)]
        del items[1]["source"]
        report = bank.report_bank(items)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["verification"], {"verified_with_case": 1, "pending": 1})
        self.assertEqual(report["source_status"], {"known": 1, "unknown": 1})
        self.assertEqual(report["revisions"]["unknown"], 3)
        self.assertEqual(report["categories"], {"integers": 2})

    def test_stage_external_list_and_revision_without_mutating_inputs(self):
        old = [entry(revision=4)]
        candidates = [entry(answer="New explanation")]
        untouched = deepcopy(candidates)
        staged = bank.stage(old, candidates, "integer-example")
        self.assertEqual(staged[0]["revision"], 5)
        self.assertEqual(staged[0]["review"]["status"], "staged")
        self.assertEqual(candidates, untouched)
        output = self.root / "staged.json"
        raw = self.bank_path.read_bytes()
        self.assertEqual(self.cli("stage", "--bank", self.bank_path, "--candidates", self.candidates,
                                  "--id", "integer-example", "--output", output), 0)
        self.assertEqual(self.bank_path.read_bytes(), raw)
        self.assertEqual(bank.load_bank(output)[0]["review"]["status"], "staged")

    def test_approval_rejects_flag_case_or_unknown_provenance_without_running(self):
        for candidate in (entry(verified=False), entry(case=None), entry(source="https://example.org"),
                          entry(source={"url": "https://example.org", "license": "unknown"}),
                          entry(case={"buggy": {"kind": "stdout", "value": "1"},
                                      "fix": {"kind": "stdout", "value": "1"}})):
            with self.subTest(candidate=candidate):
                self.write(self.candidates, [candidate])
                raw = self.bank_path.read_bytes()
                with patch.object(bank, "validate_one") as validate:
                    with self.assertRaises(ValueError):
                        self.approve()
                    validate.assert_not_called()
                self.assertEqual(self.bank_path.read_bytes(), raw)
                self.assertFalse(self.archive.exists())

    def test_approval_rejects_compile_only_failure_one_sided_and_ub_without_l4(self):
        results = [{"passed": ["L1-fix编译"], "failed": [], "skipped": []},
                   {**evidence(), "failed": ["L3-buggy行为: mismatch"]},
                   {"passed": ["L3-fix行为"], "failed": [], "skipped": []}]
        for result in results:
            with self.subTest(result=result), patch.object(bank, "validate_one", return_value=result):
                with self.assertRaises(ValueError):
                    self.approve()
        candidate = entry()
        candidate["case"]["buggy"] = {"kind": "ub"}
        self.write(self.candidates, [candidate])
        with patch.object(bank, "validate_one", return_value=evidence()):
            with self.assertRaisesRegex(ValueError, "L4"):
                self.approve()

    def test_approval_snapshots_exact_bytes_and_keeps_review_history(self):
        raw = self.bank_path.read_bytes()
        with patch.object(bank, "validate_one", return_value=evidence()) as validate:
            approved = self.approve()
        validate.assert_called_once()
        self.assertEqual(Path(approved["archive"]).name, f"bank-{hashlib.sha256(raw).hexdigest()}.json")
        self.assertEqual(Path(approved["archive"]).read_bytes(), raw)
        item = bank.load_bank(self.bank_path)[0]
        self.assertEqual(item["revision"], 2)
        self.assertEqual(item["review"]["reviewer"], "Alice")
        self.assertEqual(item["validation"], evidence())
        with patch.object(bank, "validate_one", return_value=evidence()):
            self.approve()
        item = bank.load_bank(self.bank_path)[0]
        self.assertEqual(item["revision"], 3)
        self.assertEqual([review["revision"] for review in item["review_history"]], [2, 3])

    def test_approval_detects_bank_changes_during_validation(self):
        def concurrent_change(item):
            self.write(self.bank_path, [entry(answer="Changed concurrently")])
            return evidence()
        with patch.object(bank, "validate_one", side_effect=concurrent_change):
            with self.assertRaisesRegex(ValueError, "changed during validation"):
                self.approve()
        self.assertEqual(bank.load_bank(self.bank_path)[0]["answer"], "Changed concurrently")

    def test_rollback_restores_bytes_and_optionally_archives_displaced_bank(self):
        original = self.bank_path.read_bytes()
        with patch.object(bank, "validate_one", return_value=evidence()):
            result = self.approve()
        displaced = self.bank_path.read_bytes()
        bank.rollback(result["archive"], self.bank_path, self.archive)
        self.assertEqual(self.bank_path.read_bytes(), original)
        self.assertEqual((self.archive / f"bank-{hashlib.sha256(displaced).hexdigest()}.json").read_bytes(), displaced)
        invalid = self.root / "invalid.json"
        self.write(invalid, {"not": "a list"})
        with self.assertRaises(ValueError):
            bank.rollback(invalid, self.bank_path)
        self.assertEqual(self.bank_path.read_bytes(), original)

    def test_cli_rejects_input_overwrite_and_invalid_lists(self):
        self.assertEqual(self.cli("migrate", "--bank", self.bank_path, "--output", self.bank_path), 1)
        self.write(self.candidates, {})
        self.assertEqual(self.cli("check", "--bank", self.candidates), 1)
        self.assertEqual(self.cli("check", "--bank", self.bank_path), 0)

    def test_validator_adapter_runs_real_behavior(self):
        result = bank.validate_one(entry())
        bank._require_evidence(entry(), result)
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["id"], "integer-example")


if __name__ == "__main__":
    unittest.main()
