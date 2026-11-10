"""Standalone stdlib tests: python -m unittest discover -s tests -p test_review_store.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins" / "astrbot_plugin_kouhu_oj" / "review_store.py"
)
_SPEC = importlib.util.spec_from_file_location("kouhu_review_store_test", _MODULE_PATH)
review_store = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(review_store)


class LegacyImportTests(unittest.TestCase):
    def test_legacy_import_runs_once_and_marks_missing_content(self):
        store = Store()
        store._conn.executescript("""
            CREATE TABLE attempts(id INTEGER PRIMARY KEY,scope_id TEXT,cf_key TEXT,user_id TEXT,verdict TEXT,at REAL);
            CREATE TABLE current_problem(scope_id TEXT,cf_key TEXT,opened_at REAL,status TEXT,title TEXT);
            INSERT INTO attempts VALUES(1,'g','4A','u','AC',150);
            INSERT INTO current_problem VALUES('g','4A',100,'ac','Watermelon');
        """)
        store.import_legacy_reviews()
        rows, total = store.review_history('g','u')
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['hint_level'], -1)
        self.assertIn('旧版未保存', rows[0]['submission'])
        store._conn.execute("INSERT INTO attempts VALUES(2,'g','4A','u','WA',200)")
        store.import_legacy_reviews()
        self.assertEqual(store.review_history('g','u')[1], 1)
        store.close()


class Store(review_store.ReviewStoreMixin):
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.init_reviews()

    def close(self) -> None:
        self._conn.close()


def current(opened_at: float = 100.0, **changes) -> dict:
    result = dict(cf_key="4A", title="Watermelon", opened_at=opened_at,
                  closed_at=None, status="open")
    result.update(changes)
    return result


class ReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)

    def test_scope_and_user_isolation(self) -> None:
        entry_id = self.store.note_review("group-a", current(), "alice", "yes", "AC", "ok", 30)
        self.store.note_review("group-a", current(), "bob", "no", "WA", "wrong")
        self.store.note_review("group-b", current(), "alice", "yes", "AC", "ok")
        rows, total = self.store.review_history("group-a", "alice")
        self.assertEqual(total, 1)
        self.assertEqual([row["id"] for row in rows], [entry_id])
        self.assertEqual(rows[0]["points"], 30)
        self.assertEqual(rows[0]["hint_level"], 0)
        self.assertEqual(rows[0]["cf_key"], "4A")
        self.assertEqual(rows[0]["problem_key"], "4A")
        self.assertEqual(rows[0]["title"], "Watermelon")
        self.assertEqual(self.store.review_entry("group-a", "alice", entry_id), rows[0])
        self.assertIsNone(self.store.review_entry("group-b", "alice", entry_id))
        self.assertIsNone(self.store.review_entry("group-a", "bob", entry_id))
        self.assertIsNone(self.store.review_entry("group-a", "alice", 9999))
        self.assertEqual(self.store.review_history("missing", "alice"), ([], 0))

    def test_hints_are_personal_and_frozen_at_attempt_time(self) -> None:
        with patch.object(review_store.time, "time", return_value=110):
            self.store.note_hint("group", current(), "bob", 3)
            self.store.note_hint("other-group", current(), "alice", 3)
            self.store.note_hint("group", current(101), "alice", 3)
        with patch.object(review_store.time, "time", return_value=115):
            before = self.store.note_review("group", current(), "alice", "a", "WA", "")
        with patch.object(review_store.time, "time", return_value=120):
            self.store.note_hint("group", current(), "alice", 2)
        with patch.object(review_store.time, "time", return_value=125):
            self.store.note_hint("group", current(), "alice", 1)
        with patch.object(review_store.time, "time", return_value=140):
            self.store.note_hint("group", current(), "alice", 3)
        # A recorded future hint must not be attributed to an earlier attempt.
        with patch.object(review_store.time, "time", return_value=130):
            middle = self.store.note_review("group", current(), "alice", "b", "WA", "")
        with patch.object(review_store.time, "time", return_value=150):
            after = self.store.note_review("group", current(), "alice", "c", "AC", "")
        for entry_id, level in ((before, 0), (middle, 2), (after, 3)):
            self.assertEqual(self.store.review_entry("group", "alice", entry_id)["hint_level"], level)

    def test_round_identity_and_metadata_sync(self) -> None:
        first = self.store.ensure_round("group", current())
        self.assertEqual(first, self.store.ensure_round("group", current()))
        second = self.store.ensure_round("group", current(200))
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, self.store.ensure_round("other-group", current()))
        entry_id = self.store.note_review("group", current(), "alice", "x", "WA", "")
        closed = current(title="Updated", status="solved", closed_at=180)
        self.assertEqual(self.store.ensure_round("group", closed), first)
        entry = self.store.review_entry("group", "alice", entry_id)
        self.assertEqual((entry["title"], entry["status"], entry["closed_at"]),
                         ("Updated", "solved", 180))
        self.assertEqual(entry["opened_at"], 100)
        self.store.ensure_round("group", current())
        self.assertIsNone(self.store.review_entry("group", "alice", entry_id)["closed_at"])

    def test_history_pagination_and_ties(self) -> None:
        with patch.object(review_store.time, "time", return_value=120):
            ids = [self.store.note_review("g", current(), "u", str(n), "WA", "")
                   for n in range(7)]
        rows, total = self.store.review_history("g", "u")
        self.assertEqual(total, 7)
        self.assertEqual([row["id"] for row in rows], ids[::-1][:5])
        rows, total = self.store.review_history("g", "u", page=2)
        self.assertEqual(total, 7)
        self.assertEqual([row["id"] for row in rows], ids[::-1][5:])
        self.assertEqual(self.store.review_history("g", "u", page=3), ([], 7))
        self.assertEqual(len(self.store.review_history("g", "u", page=0, page_size=0)[0]), 1)

    def test_problem_groups_rounds_and_keeps_latest_personal_metadata(self) -> None:
        with patch.object(review_store.time, "time", return_value=120):
            first = self.store.note_review("g", current(), "u", "first", "WA", "wrong", 2)
        with patch.object(review_store.time, "time", return_value=210):
            self.store.note_hint("g", current(200), "u", 2)
        with patch.object(review_store.time, "time", return_value=220):
            latest = self.store.note_review(
                "g", current(200, title="Second round", status="ac"),
                "u", "second", "AC", "correct", 10,
            )
        # A newer round with no personal submission still supplies current status.
        self.store.ensure_round("g", current(300, title="Third round", status="open"))
        self.store.ensure_round("other", current(400, status="hidden"))
        rows, total = self.store.review_problems("g", "u")
        self.assertEqual(total, 1)
        row = rows[0]
        self.assertEqual(row["id"], latest)
        self.assertEqual((row["title"], row["opened_at"]), ("Second round", 200))
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["submission_count"], 2)
        self.assertEqual(row["verdicts"], ["AC", "WA"])
        self.assertEqual(row["total_points"], 12)
        self.assertEqual(row["hint_level"], 2)
        self.assertIs(row["legacy_unknown"], False)
        self.assertEqual(self.store.review_problem("g", "u", "4A"),
                         self.store.review_entry("g", "u", latest))
        entries, total = self.store.review_problem_entries("g", "u", "4A")
        self.assertEqual(total, 2)
        self.assertEqual([entry["id"] for entry in entries], [latest, first])

    def test_problem_queries_are_personal_and_scope_isolated(self) -> None:
        mine = self.store.note_review("g", current(), "u", "mine", "WA", "", 3)
        self.store.note_review("g", current(), "other", "theirs", "AC", "", 50)
        self.store.note_review("other", current(), "u", "elsewhere", "AC", "", 50)
        self.store.note_review("g", current(cf_key="5A"), "other", "private", "AC", "")
        rows, total = self.store.review_problems("g", "u")
        self.assertEqual(total, 1)
        self.assertEqual((rows[0]["id"], rows[0]["submission_count"],
                          rows[0]["total_points"], rows[0]["verdicts"]),
                         (mine, 1, 3, ["WA"]))
        self.assertEqual(self.store.review_problem_entries("g", "u", "4A")[1], 1)
        for scope, user, key in (("missing", "u", "4A"),
                                 ("g", "missing", "4A"), ("g", "u", "5A")):
            self.assertIsNone(self.store.review_problem(scope, user, key))
            self.assertEqual(self.store.review_problem_entries(scope, user, key), ([], 0))
        self.assertEqual(self.store.review_problems("missing", "u"), ([], 0))
        self.assertIsNone(self.store.review_problem("g", "u", "4a"))

    def test_problem_pagination_latest_selection_and_ties(self) -> None:
        with patch.object(review_store.time, "time", return_value=500):
            ids = [self.store.note_review("g", current(cf_key=f"{n}A"), "u", str(n), "WA", "")
                   for n in range(1, 8)]
            latest = self.store.note_review("g", current(200, cf_key="1A"), "u", "retry", "AC", "")
        rows, total = self.store.review_problems("g", "u")
        self.assertEqual(total, 7)
        self.assertEqual([row["id"] for row in rows], [latest, *ids[:2:-1]])
        self.assertEqual([row["id"] for row in self.store.review_problems("g", "u", page=2)[0]],
                         ids[1:3][::-1])
        self.assertEqual(self.store.review_problems("g", "u", page=3), ([], 7))
        self.assertEqual(len(self.store.review_problems("g", "u", page=0, page_size=0)[0]), 1)
        self.assertEqual(self.store.review_problem("g", "u", "1A")["id"], latest)
        self.assertEqual([row["id"] for row in self.store.review_problem_entries(
            "g", "u", "1A", page=2, page_size=1)[0]], [ids[0]])
        self.assertEqual(self.store.review_problem_entries("g", "u", "1A", page=3, page_size=1), ([], 2))
        self.assertEqual(self.store.review_problem_entries("g", "u", "1A", page=0, page_size=0)[0][0]["id"], latest)

    def test_problem_legacy_flags_points_and_distinct_verdicts(self) -> None:
        rid = self.store.ensure_round("g", current())
        self.store._conn.execute(
            """INSERT INTO review_entries
                (round_id,user_id,submission,verdict,comment,points,hint_level,at)
                VALUES (?, 'u', 'legacy', 'WA', '', 99, -1, 100)""", (rid,),
        )
        row = self.store.review_problems("g", "u")[0][0]
        self.assertEqual((row["total_points"], row["hint_level"]), (0, -1))
        self.assertIs(row["legacy_unknown"], True)
        with patch.object(review_store.time, "time", return_value=120):
            self.store.note_review("g", current(), "u", "retry", "WA", "", 3)
        row = self.store.review_problems("g", "u")[0][0]
        self.assertEqual((row["submission_count"], row["verdicts"]), (2, ["WA"]))
        self.assertEqual((row["total_points"], row["hint_level"]), (3, 0))
        self.assertIs(row["legacy_unknown"], True)

    def test_solutions_are_per_round_and_replaceable(self) -> None:
        first = self.store.ensure_round("g", current())
        second = self.store.ensure_round("g", current(200))
        self.assertIsNone(self.store.solution(first))
        with patch.object(review_store.time, "time", return_value=300):
            self.store.save_solution(first, "解法", "model")
        self.assertEqual(self.store.solution(first),
                         dict(round_id=first, text="解法", source="model", at=300))
        self.assertIsNone(self.store.solution(second))
        with patch.object(review_store.time, "time", return_value=400):
            self.store.save_solution(first, "official text", "editorial")
        self.assertEqual(self.store.solution(first),
                         dict(round_id=first, text="official text", source="editorial", at=400))

    def test_reopen_and_additive_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "store.sqlite")
            original = sqlite3.connect(path)
            original.execute("PRAGMA user_version = 23")
            original.execute("CREATE TABLE players (name TEXT)")
            original.execute("INSERT INTO players VALUES ('existing')")
            original.commit()
            original.close()
            store = Store(path)
            try:
                store.note_hint("g", current(), "u", 2)
                entry_id = store.note_review("g", current(), "u", "answer", "AC", "comment", 5)
                round_id = store.ensure_round("g", current())
                store.save_solution(round_id, "text", "source")
            finally:
                store.close()
            reopened = Store(path)
            try:
                reopened.init_reviews()
                self.assertEqual(reopened._conn.execute("PRAGMA user_version").fetchone()[0], 23)
                self.assertEqual(reopened._conn.execute("SELECT name FROM players").fetchone()[0], "existing")
                self.assertEqual(reopened.ensure_round("g", current()), round_id)
                entry = reopened.review_entry("g", "u", entry_id)
                self.assertEqual((entry["submission"], entry["hint_level"], entry["points"]), ("answer", 2, 5))
                self.assertEqual(reopened.review_history("g", "u")[1], 1)
                self.assertEqual(reopened.solution(round_id)["text"], "text")
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
