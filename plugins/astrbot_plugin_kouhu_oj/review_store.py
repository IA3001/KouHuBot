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
# e.* 自动带出 review_entries 的全部列，新增的 counterexample 无需改这里。


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
                counterexample TEXT NOT NULL DEFAULT '',
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
            # 老库只加列：不重建表、不改 PRAGMA user_version，重复执行也安全。
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(review_entries)")}
            if "counterexample" not in columns:
                self._conn.execute(
                    "ALTER TABLE review_entries ADD COLUMN counterexample TEXT NOT NULL DEFAULT ''"
                )

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
        verdict: str, comment: str, points: int = 0, counterexample: str = "",
    ) -> int:
        """Persist an attempt with the user's maximum hint level at its time.

        ``counterexample`` is optional: old call sites keep working and simply
        store an empty string.
        """
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
                     points, hint_level, counterexample, at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (round_id, user_id, submission, verdict, comment,
                 points, hint_level, counterexample or "", now),
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

    def review_problem(
        self, scope: str, user_id: str, key: str,
    ) -> dict | None:
        """Return the newest personal entry for an already-normalized CF key."""
        row = self._conn.execute(
            _ENTRY_SELECT + """ WHERE r.scope_id = ? AND e.user_id = ?
                AND r.problem_key = ? ORDER BY e.at DESC, e.id DESC LIMIT 1""",
            (scope, user_id, key),
        ).fetchone()
        return dict(row) if row is not None else None

    def review_problems(
        self, scope: str, user_id: str, page: int = 1, page_size: int = 5,
    ) -> tuple[list[dict], int]:
        """Page personal problems by newest entry, grouping all their rounds.

        Entry metadata belongs to the latest personal attempt; ``status`` tracks
        the latest round in this scope, including rounds the user has not tried.
        ``verdicts`` lists distinct verdicts in newest-attempt-first order.
        Negative hint levels mark legacy entries, whose points are unknown and
        excluded from ``total_points``. ``hint_level`` is the maximum recorded
        level (or -1 when all entries are legacy).
        """
        page, page_size = max(1, page), max(1, page_size)
        total = self._conn.execute(
            """SELECT COUNT(DISTINCT r.problem_key)
                FROM review_entries e JOIN review_rounds r ON r.id = e.round_id
                WHERE r.scope_id = ? AND e.user_id = ?""",
            (scope, user_id),
        ).fetchone()[0]
        rows = self._conn.execute(
            """WITH entries AS (
                SELECT e.*, r.scope_id, r.problem_key, r.problem_key AS cf_key,
                       r.title, r.opened_at, r.closed_at,
                       (SELECT latest.status FROM review_rounds latest
                        WHERE latest.scope_id = r.scope_id
                          AND latest.problem_key = r.problem_key
                        ORDER BY latest.opened_at DESC, latest.id DESC LIMIT 1) AS status,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.problem_key ORDER BY e.at DESC, e.id DESC
                       ) AS entry_rank,
                       COUNT(*) OVER problem AS submission_count,
                       SUM(CASE WHEN e.hint_level >= 0 THEN e.points ELSE 0 END)
                           OVER problem AS total_points,
                       MAX(e.hint_level) OVER problem AS max_hint_level,
                       MAX(e.hint_level < 0) OVER problem AS legacy_unknown
                FROM review_entries e JOIN review_rounds r ON r.id = e.round_id
                WHERE r.scope_id = ? AND e.user_id = ?
                WINDOW problem AS (PARTITION BY r.problem_key)
            )
            SELECT * FROM entries WHERE entry_rank = 1
            ORDER BY at DESC, id DESC LIMIT ? OFFSET ?""",
            (scope, user_id, page_size, (page - 1) * page_size),
        ).fetchall()
        problems = [dict(row) for row in rows]
        if problems:
            verdicts = {row["cf_key"]: [] for row in problems}
            placeholders = ",".join("?" for _ in problems)
            verdict_rows = self._conn.execute(
                """SELECT r.problem_key, e.verdict FROM review_entries e
                    JOIN review_rounds r ON r.id = e.round_id
                    WHERE r.scope_id = ? AND e.user_id = ?
                      AND r.problem_key IN (""" + placeholders + """ )
                    ORDER BY e.at DESC, e.id DESC""",
                (scope, user_id, *verdicts),
            ).fetchall()
            for row in verdict_rows:
                values = verdicts[row["problem_key"]]
                if row["verdict"] not in values:
                    values.append(row["verdict"])
            for row in problems:
                row.pop("entry_rank")
                row["hint_level"] = row.pop("max_hint_level")
                row["legacy_unknown"] = bool(row["legacy_unknown"])
                row["verdicts"] = verdicts[row["cf_key"]]
        return problems, int(total)

    def review_problem_entries(
        self, scope: str, user_id: str, key: str,
        page: int = 1, page_size: int = 3,
    ) -> tuple[list[dict], int]:
        """Page this user's entries for a normalized key across all scope rounds."""
        page, page_size = max(1, page), max(1, page_size)
        filters = " WHERE r.scope_id = ? AND e.user_id = ? AND r.problem_key = ?"
        total = self._conn.execute(
            """SELECT COUNT(*) FROM review_entries e
                JOIN review_rounds r ON r.id = e.round_id""" + filters,
            (scope, user_id, key),
        ).fetchone()[0]
        rows = self._conn.execute(
            _ENTRY_SELECT + filters + " ORDER BY e.at DESC, e.id DESC LIMIT ? OFFSET ?",
            (scope, user_id, key, page_size, (page - 1) * page_size),
        ).fetchall()
        return [dict(row) for row in rows], int(total)

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
