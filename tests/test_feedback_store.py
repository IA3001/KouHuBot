"""Standalone tests: python -m unittest discover -s tests -p test_feedback_store.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


_MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "astrbot_plugin_kouhu_oj" / "feedback_store.py"
_SPEC = importlib.util.spec_from_file_location("kouhu_feedback_store_test", _MODULE_PATH)
feedback_store = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(feedback_store)


class Store(feedback_store.FeedbackStoreMixin):
    def __init__(self, path=":memory:"):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.init_feedback()

    def close(self):
        self._conn.close()


def entry(id=1):
    return dict(id=id, submission="my answer", verdict="WA", comment="original judgment")


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)

    def test_scope_user_filters_and_pagination(self):
        with patch.object(feedback_store.time, "time", return_value=100):
            first = self.store.create_ticket("g", "alice", "report", "4A", "bad statement")
            second = self.store.create_ticket("g", "bob", "report", "4A", "typo")
            self.store.create_ticket("other", "alice", "report", "4A", "typo")
        self.assertEqual(self.store.ticket("g", first, "alice")["reason"], "bad statement")
        self.assertIsNone(self.store.ticket("other", first))
        self.assertIsNone(self.store.ticket("g", first, "bob"))
        self.assertIsNone(self.store.ticket("g", 999))
        rows, total = self.store.tickets("g")
        self.assertEqual((total, [row["id"] for row in rows]), (2, [second, first]))
        self.assertEqual(self.store.tickets("g", "alice")[1], 1)
        self.assertEqual(self.store.tickets("g", page=2, size=1)[0][0]["id"], first)
        self.assertEqual(self.store.tickets("g", page=3, size=1), ([], 2))
        self.assertEqual(len(self.store.tickets("g", page=0, size=0)[0]), 1)
        self.assertEqual(self.store.tickets("missing"), ([], 0))
        self.store.resolve_ticket("g", second, "admin", "resolved", "fixed")
        self.assertEqual(self.store.tickets("g", pending_only=True)[1], 1)

    def test_duplicate_reports_only_while_pending_and_in_same_scope_user(self):
        first = self.store.create_ticket("g", "alice", "report", "4A", "wrong")
        with self.assertRaises(ValueError):
            self.store.create_ticket("g", "alice", "report", "4A", "again")
        self.store.create_ticket("g", "bob", "report", "4A", "wrong")
        self.store.create_ticket("other", "alice", "report", "4A", "wrong")
        self.store.resolve_ticket("g", first, "admin", "rejected", "correct statement")
        second = self.store.create_ticket("g", "alice", "report", "4A", "new evidence")
        self.assertNotEqual(first, second)

    def test_duplicate_appeals_remain_blocked_after_closure_and_snapshot_is_frozen(self):
        original = entry()
        first = self.store.create_ticket("g", "alice", "appeal", "4A", "rejudge", round_id=7, entry=original)
        original["comment"] = "changed"
        ticket = self.store.ticket("g", first)
        self.assertEqual((ticket["entry_id"], ticket["round_id"]), (1, 7))
        self.assertEqual((ticket["original_submission"], ticket["original_verdict"], ticket["original_comment"]),
                         ("my answer", "WA", "original judgment"))
        with self.assertRaises(ValueError):
            self.store.create_ticket("g", "alice", "appeal", "4A", "again", entry=entry())
        self.store.resolve_ticket("g", first, "admin", "resolved", "agreed")
        with self.assertRaises(ValueError):
            self.store.create_ticket("g", "alice", "appeal", "different", "again", entry=entry())
        self.store.create_ticket("g", "bob", "appeal", "4A", "review", entry=entry())
        self.store.create_ticket("other", "alice", "appeal", "4A", "review", entry=entry())
        self.store.create_ticket("g", "alice", "appeal", "4A", "review", entry=entry(2))
        for kind, snapshot in (("unknown", None), ("appeal", None), ("appeal", {})):
            with self.assertRaises(ValueError):
                self.store.create_ticket("g", "alice", kind, "4A", "invalid", entry=snapshot)

    def test_rolling_hour_rate_uses_database_across_kinds_and_closed_tickets(self):
        with patch.object(feedback_store.time, "time", return_value=10000):
            first = self.store.create_ticket("g", "alice", "appeal", "4A", "review", entry=entry())
            self.store.resolve_ticket("g", first, "admin", "resolved", "done")
            for n in range(4):
                self.store.create_ticket("g", "alice", "report", str(n), "issue")
            with self.assertRaises(ValueError):
                self.store.create_ticket("g", "alice", "report", "sixth", "issue")
            self.store.create_ticket("other", "alice", "report", "sixth", "issue")
            self.store.create_ticket("g", "bob", "report", "sixth", "issue")
        with patch.object(feedback_store.time, "time", return_value=13599):
            with self.assertRaises(ValueError):
                self.store.create_ticket("g", "alice", "report", "sixth", "issue")
        with patch.object(feedback_store.time, "time", return_value=13600):
            self.store.create_ticket("g", "alice", "report", "sixth", "issue")
        self.assertEqual(self.store.tickets("g", "alice")[1], 6)

    def test_review_only_changes_text_and_resolution_audits_once(self):
        with patch.object(feedback_store.time, "time", return_value=100):
            id = self.store.create_ticket("g", "alice", "report", "4A", "issue")
        before = self.store.ticket("g", id)
        self.store.set_review("g", id, "draft")
        self.assertEqual(self.store.ticket("g", id), {**before, "review_text": "draft"})
        for operation in (
            lambda: self.store.set_review("other", id, "bad"),
            lambda: self.store.resolve_ticket("other", id, "admin", "resolved", "bad"),
            lambda: self.store.resolve_ticket("g", id, "admin", "pending", "bad"),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.store.actions("g", id), [])
        with patch.object(feedback_store.time, "time", return_value=200):
            self.store.resolve_ticket("g", id, "admin", "resolved", "fixed")
        self.assertEqual(self.store.ticket("g", id)["updated_at"], 200)
        self.assertEqual(self.store.actions("g", id), [dict(id=1, ticket_id=id, actor="admin", action="resolved", note="fixed", at=200)])
        self.assertEqual(self.store.actions("other", id), [])
        with self.assertRaises(ValueError):
            self.store.resolve_ticket("g", id, "admin", "rejected", "again")
        self.assertEqual(len(self.store.actions("g", id)), 1)

    def test_resolution_rolls_back_if_audit_insert_fails(self):
        id = self.store.create_ticket("g", "alice", "report", "4A", "issue")
        self.store._conn.execute("""CREATE TRIGGER fail_audit BEFORE INSERT ON feedback_actions
            BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.resolve_ticket("g", id, "admin", "resolved", "fixed")
        self.assertEqual(self.store.ticket("g", id)["status"], "pending")
        self.assertEqual(self.store.actions("g", id), [])

    def test_pause_upsert_isolation_and_audit(self):
        self.store.pause("g", "4A", "admin", "bad statement")
        self.store.pause("other", "4A", "moderator", "other issue")
        self.store.pause("g", "4A", "moderator", "updated")
        self.assertEqual(self.store.paused_keys("g"), {"4A"})
        paused = dict(self.store._conn.execute("SELECT * FROM paused_problems WHERE scope_id='g'").fetchone())
        self.assertEqual((paused["actor"], paused["reason"]), ("moderator", "updated"))
        self.store.unpause("g", "4A", "admin", "fixed")
        self.assertEqual(self.store.paused_keys("g"), set())
        self.assertEqual(self.store.paused_keys("other"), {"4A"})
        actions = self.store._conn.execute("SELECT * FROM feedback_actions ORDER BY id").fetchall()
        self.assertEqual([row["action"] for row in actions], ["pause", "pause", "pause", "unpause"])
        self.assertTrue(all(row["ticket_id"] is None for row in actions))
        self.assertEqual(json.loads(actions[-1]["note"]), dict(scope_id="g", problem_key="4A", reason="fixed"))

    def test_reopen_persistence_additive_schema_and_database_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "store.sqlite")
            with sqlite3.connect(path) as conn:
                conn.execute("PRAGMA user_version = 23")
                conn.execute("CREATE TABLE schema_version(version INTEGER)")
                conn.execute("INSERT INTO schema_version VALUES (9)")
                conn.execute("CREATE TABLE players(name TEXT)")
                conn.execute("INSERT INTO players VALUES ('existing')")
            store = Store(path)
            with patch.object(feedback_store.time, "time", return_value=100):
                id = store.create_ticket("g", "alice", "appeal", "4A", "review", entry=entry())
                store.set_review("g", id, "draft")
                store.resolve_ticket("g", id, "admin", "rejected", "confirmed")
                store.pause("g", "4A", "admin", "investigating")
                for n in range(4):
                    store.create_ticket("g", "alice", "report", str(n), "issue")
            store.close()
            reopened = Store(path)
            try:
                reopened.init_feedback()
                self.assertEqual(reopened._conn.execute("PRAGMA user_version").fetchone()[0], 23)
                self.assertEqual(reopened._conn.execute("SELECT version FROM schema_version").fetchone()[0], 9)
                self.assertEqual(reopened._conn.execute("SELECT name FROM players").fetchone()[0], "existing")
                self.assertEqual(reopened.ticket("g", id)["review_text"], "draft")
                self.assertEqual(reopened.ticket("g", id)["original_comment"], "original judgment")
                self.assertEqual(reopened.actions("g", id)[0]["action"], "rejected")
                self.assertEqual(reopened.paused_keys("g"), {"4A"})
                with patch.object(feedback_store.time, "time", return_value=200):
                    with self.assertRaises(ValueError):
                        reopened.create_ticket("g", "alice", "report", "sixth", "issue")
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
