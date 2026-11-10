"""Additive review persistence for an OjStore's existing SQLite connection.

Call ``init_reviews()`` after opening/migrating the store. This mixin never
changes ``PRAGMA user_version`` or any of the scoring tables.
"""

from __future__ import annotations

import sqlite3
import time


_ENTRY_SELECT = """
    SELECT e.*, r.scope_id, r.problem_key, r.problem_key AS cf_key,
           r.title, r.opened_at, r.closed_at, r.status
    FROM review_entries AS e
    JOIN review_rounds AS r ON r.id = e.round_id
"""


class ReviewStoreMixin:
    """Review APIs sharing the host store's ``sqlite3.Row`` connection."""

    _conn: sqlite3.Connection

    def init_reviews(self) -> None:
        """Create review tables and indexes idempotently, preserving old data."""
        statements = (
            """CREATE TABLE IF NOT EXISTS review_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_id TEXT NOT NULL,
                problem_key TEXT NOT NULL,
                title TEXT,
                opened_at REAL NOT NULL,
                closed_at REAL,
                status TEXT NOT NULL,
                UNIQUE(scope_id, problem_key, opened_at)
            )""",
            """CREATE TABLE IF NOT EXISTS review_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL REFERENCES review_rounds(id),
                user_id TEXT NOT NULL,
                submission TEXT NOT NULL,
                verdict TEXT NOT NULL,
                comment TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                hint_level INTEGER NOT NULL DEFAULT 0,
                at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS review_hint_uses (
                round_id INTEGER NOT NULL REFERENCES review_rounds(id),
                user_id TEXT NOT NULL,
                level INTEGER NOT NULL,
                at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS review_solutions (
                round_id INTEGER PRIMARY KEY REFERENCES review_rounds(id),
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS review_entries_user_at
                ON review_entries(user_id, at DESC, id DESC)""",
            """CREATE INDEX IF NOT EXISTS review_hint_uses_round_user_at
                ON review_hint_uses(round_id, user_id, at)""",
        )
        with self._conn:
            for statement in statements:
                self._conn.execute(statement)

    def import_legacy_reviews(self) -> None:
        """旧版没有保存作答和判词，只导入原有判定，不伪造缺失内容。"""
        with self._conn:
            self._conn.execute("CREATE TABLE IF NOT EXISTS review_legacy_imports (attempt_id INTEGER PRIMARY KEY)")
            if self._conn.execute("SELECT 1 FROM review_legacy_imports WHERE attempt_id=-1").fetchone():
                return
            rows = self._conn.execute(
                "SELECT a.* FROM attempts a LEFT JOIN review_legacy_imports i ON i.attempt_id=a.id WHERE i.attempt_id IS NULL"
            ).fetchall()
            for row in rows:
                a = dict(row)
                current = self._conn.execute("SELECT * FROM current_problem WHERE scope_id=? AND cf_key=?", (a["scope_id"], a["cf_key"])).fetchone()
                c = dict(current) if current and (current["opened_at"] or 0) <= a["at"] else {
                    "cf_key": a["cf_key"], "opened_at": 0, "status": "legacy", "title": "旧版题目"
                }
                rid = self._ensure_review_round(a["scope_id"], c)
                self._conn.execute(
                    "INSERT INTO review_entries(round_id,user_id,submission,verdict,comment,points,hint_level,at) VALUES(?,?,?,?,?,0,-1,?)",
                    (rid,a["user_id"],"旧版未保存作答",a["verdict"],"旧版未保存判词与本次得分，历史提示使用情况未知",a["at"]),
                )
                self._conn.execute("INSERT INTO review_legacy_imports VALUES(?)", (a["id"],))
            self._conn.execute("INSERT INTO review_legacy_imports VALUES(-1)")

    def _ensure_review_round(self, scope: str, current: dict) -> int:
        self._conn.execute(
            """INSERT INTO review_rounds
                (scope_id, problem_key, title, opened_at, closed_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id, problem_key, opened_at) DO UPDATE SET
                    title = excluded.title,
                    closed_at = excluded.closed_at,
                    status = excluded.status""",
            (
                scope, current["cf_key"], current.get("title"),
                current["opened_at"], current.get("closed_at"),
                current.get("status", "open"),
            ),
        )
        row = self._conn.execute(
            """SELECT id FROM review_rounds
                WHERE scope_id = ? AND problem_key = ? AND opened_at = ?""",
            (scope, current["cf_key"], current["opened_at"]),
        ).fetchone()
        return int(row[0])

    def ensure_round(self, scope: str, current: dict) -> int:
        """Find/create a round and sync its title, status and closing timestamp.

        A round is identified by explicit scope, ``cf_key`` and ``opened_at``.
        Reissuing the same problem at a different time creates a new round.
        """
        with self._conn:
            return self._ensure_review_round(scope, current)

    def note_review(
        self, scope: str, current: dict, user_id: str, submission: str,
        verdict: str, comment: str, points: int = 0,
    ) -> int:
        """Persist an attempt with the user's maximum hint level at its time."""
        now = time.time()
        with self._conn:
            round_id = self._ensure_review_round(scope, current)
            hint_level = self._conn.execute(
                """SELECT COALESCE(MAX(level), 0) FROM review_hint_uses
                    WHERE round_id = ? AND user_id = ? AND at <= ?""",
                (round_id, user_id, now),
            ).fetchone()[0]
            cursor = self._conn.execute(
                """INSERT INTO review_entries
                    (round_id, user_id, submission, verdict, comment,
                     points, hint_level, at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (round_id, user_id, submission, verdict, comment,
                 points, hint_level, now),
            )
            return int(cursor.lastrowid)

    def note_hint(
        self, scope: str, current: dict, user_id: str, level: int,
    ) -> None:
        """Record a hint request for this user and round at the current time."""
        now = time.time()
        with self._conn:
            round_id = self._ensure_review_round(scope, current)
            self._conn.execute(
                """INSERT INTO review_hint_uses (round_id, user_id, level, at)
                    VALUES (?, ?, ?, ?)""",
                (round_id, user_id, level, now),
            )

    def review_history(
        self, scope: str, user_id: str, page: int = 1, page_size: int = 5,
    ) -> tuple[list[dict], int]:
        """Return newest attempts with round metadata and total entry count.

        Pages are one-based. Nonpositive pages/sizes are clamped to one.
        Results include ``problem_key`` and its existing-store alias ``cf_key``.
        """
        page, page_size = max(1, page), max(1, page_size)
        filters = " WHERE r.scope_id = ? AND e.user_id = ?"
        total = self._conn.execute(
            """SELECT COUNT(*) FROM review_entries AS e
                JOIN review_rounds AS r ON r.id = e.round_id""" + filters,
            (scope, user_id),
        ).fetchone()[0]
        rows = self._conn.execute(
            _ENTRY_SELECT + filters + " ORDER BY e.at DESC, e.id DESC LIMIT ? OFFSET ?",
            (scope, user_id, page_size, (page - 1) * page_size),
        ).fetchall()
        return [dict(row) for row in rows], int(total)

    def review_entry(
        self, scope: str, user_id: str, entry_id: int,
    ) -> dict | None:
        """Return an attempt only when both scope and owner match."""
        row = self._conn.execute(
            _ENTRY_SELECT + " WHERE r.scope_id = ? AND e.user_id = ? AND e.id = ?",
            (scope, user_id, entry_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def review_hint_stats(self, scope: str, user_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT e.hint_level,COUNT(*) AS n FROM review_entries e JOIN review_rounds r ON r.id=e.round_id WHERE r.scope_id=? AND e.user_id=? AND e.verdict='AC' GROUP BY e.hint_level",
            (scope, user_id),
        ).fetchall()
        return {"requested": sum(r["n"] for r in rows if r["hint_level"] > 0),
                "unrequested": sum(r["n"] for r in rows if r["hint_level"] == 0),
                "unknown": sum(r["n"] for r in rows if r["hint_level"] < 0)}

    def solution(self, round_id: int) -> dict | None:
        """Return a round's cached solution, or None if none was saved."""
        row = self._conn.execute(
            "SELECT * FROM review_solutions WHERE round_id = ?", (round_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def save_solution(self, round_id: int, text: str, source: str) -> None:
        """Save/replace a round's solution text, source and generation time."""
        with self._conn:
            self._conn.execute(
                """INSERT INTO review_solutions (round_id, text, source, at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(round_id) DO UPDATE SET
                        text = excluded.text, source = excluded.source,
                        at = excluded.at""",
                (round_id, text, source, time.time()),
            )
