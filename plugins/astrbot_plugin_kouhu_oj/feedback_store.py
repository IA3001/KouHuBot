"""Additive feedback persistence on a host store's SQLite Row connection."""

from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import time


class FeedbackStoreMixin:
    """Feedback tickets and scoped problem pauses, independent of schema versions."""

    _conn: sqlite3.Connection

    @contextmanager
    def _feedback_transaction(self):
        # Acquire the writer lock before checking limits or pending state so two
        # connections cannot both pass a check and then create/close a ticket.
        # Savepoints also keep a failed operation atomic inside a caller's txn.
        nested = self._conn.in_transaction
        if nested:
            self._conn.execute("SAVEPOINT feedback_operation")
        else:
            self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            if nested:
                self._conn.execute("RELEASE SAVEPOINT feedback_operation")
            else:
                self._conn.commit()
        except BaseException:
            if nested:
                self._conn.execute("ROLLBACK TO SAVEPOINT feedback_operation")
                self._conn.execute("RELEASE SAVEPOINT feedback_operation")
            else:
                self._conn.rollback()
            raise

    def init_feedback(self) -> None:
        """Create only feedback tables/indexes; preserve all existing store data."""
        statements = (
            """CREATE TABLE IF NOT EXISTS feedback_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('report', 'appeal')),
                problem_key TEXT NOT NULL,
                round_id INTEGER,
                entry_id INTEGER,
                reason TEXT NOT NULL,
                original_submission TEXT,
                original_verdict TEXT,
                original_comment TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'resolved', 'rejected')),
                review_text TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS feedback_appeal_entry
                ON feedback_tickets(scope_id, user_id, kind, entry_id)
                WHERE kind = 'appeal'""",
            """CREATE UNIQUE INDEX IF NOT EXISTS feedback_pending_report
                ON feedback_tickets(scope_id, user_id, problem_key)
                WHERE kind = 'report' AND status = 'pending'""",
            """CREATE INDEX IF NOT EXISTS feedback_user_created
                ON feedback_tickets(scope_id, user_id, created_at)""",
            """CREATE TABLE IF NOT EXISTS feedback_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER REFERENCES feedback_tickets(id),
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                note TEXT NOT NULL,
                at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS paused_problems (
                scope_id TEXT NOT NULL,
                problem_key TEXT NOT NULL,
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                at REAL NOT NULL,
                PRIMARY KEY(scope_id, problem_key)
            )""",
        )
        with self._feedback_transaction():
            for statement in statements:
                self._conn.execute(statement)

    def create_ticket(
        self, scope: str, user: str, kind: str, key: str, reason: str,
        round_id: int | None = None, entry: dict | sqlite3.Row | None = None,
    ) -> int:
        """Create a report or an appeal with a frozen copy of its review entry.

        ``entry`` has ``id``, ``submission``, ``verdict`` and ``comment`` keys.
        At most five tickets may be created per scope/user in a rolling hour,
        including tickets that have since been closed.
        """
        if kind not in {"report", "appeal"}:
            raise ValueError("kind must be report or appeal")
        snapshot = dict(entry) if entry is not None else {}
        if kind == "appeal" and snapshot.get("id") is None:
            raise ValueError("an appeal requires a review entry")
        now = time.time()
        with self._feedback_transaction():
            count = self._conn.execute(
                """SELECT COUNT(*) FROM feedback_tickets
                    WHERE scope_id = ? AND user_id = ? AND created_at > ?""",
                (scope, user, now - 3600),
            ).fetchone()[0]
            if count >= 5:
                raise ValueError("每条线路每小时最多创建5张工单，请稍后再试")
            try:
                cursor = self._conn.execute(
                    """INSERT INTO feedback_tickets
                        (scope_id, user_id, kind, problem_key, round_id, entry_id,
                         reason, original_submission, original_verdict,
                         original_comment, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (scope, user, kind, key, round_id, snapshot.get("id"), reason,
                     snapshot.get("submission"), snapshot.get("verdict"),
                     snapshot.get("comment"), now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("重复工单：同题待处理反馈不重复登记；每条提交记录只能申诉一次") from exc
            return int(cursor.lastrowid)

    def ticket(self, scope: str, id: int, user: str | None = None) -> dict | None:
        """Return a scoped ticket, optionally restricted to its owner."""
        query = "SELECT * FROM feedback_tickets WHERE scope_id = ? AND id = ?"
        params = [scope, id]
        if user is not None:
            query += " AND user_id = ?"
            params.append(user)
        row = self._conn.execute(query, params).fetchone()
        return dict(row) if row is not None else None

    def tickets(
        self, scope: str, user: str | None = None, page: int = 1,
        size: int = 5, pending_only: bool = False,
    ) -> tuple[list[dict], int]:
        """Return newest-first tickets and total count; pages/sizes start at one."""
        page, size = max(1, page), max(1, size)
        filters, params = " WHERE scope_id = ?", [scope]
        if user is not None:
            filters += " AND user_id = ?"
            params.append(user)
        if pending_only:
            filters += " AND status = 'pending'"
        with self._feedback_transaction():
            total = self._conn.execute(
                "SELECT COUNT(*) FROM feedback_tickets" + filters, params,
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT * FROM feedback_tickets" + filters
                + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, size, (page - 1) * size),
            ).fetchall()
        return [dict(row) for row in rows], int(total)

    def set_review(self, scope: str, id: int, text: str) -> None:
        """Update only the review text, preserving status, timestamps and snapshot."""
        with self._feedback_transaction():
            cursor = self._conn.execute(
                "UPDATE feedback_tickets SET review_text = ? WHERE scope_id = ? AND id = ?",
                (text, scope, id),
            )
            if not cursor.rowcount:
                raise ValueError("feedback ticket not found")

    def resolve_ticket(
        self, scope: str, id: int, actor: str, status: str, note: str,
    ) -> None:
        """Close a pending ticket and atomically append its moderation action."""
        if status not in {"resolved", "rejected"}:
            raise ValueError("status must be resolved or rejected")
        now = time.time()
        with self._feedback_transaction():
            cursor = self._conn.execute(
                """UPDATE feedback_tickets SET status = ?, updated_at = ?
                    WHERE scope_id = ? AND id = ? AND status = 'pending'""",
                (status, now, scope, id),
            )
            if not cursor.rowcount:
                raise ValueError("工单不存在或已处理，不能重复关闭")
            self._conn.execute(
                "INSERT INTO feedback_actions(ticket_id, actor, action, note, at) VALUES (?, ?, ?, ?, ?)",
                (id, actor, status, note, now),
            )

    def _pause_action(self, scope: str, key: str, actor: str, action: str, reason: str, at: float) -> None:
        # Pause actions have no ticket: their durable note carries the scope/key
        # so the audit remains meaningful after the paused row is deleted.
        note = json.dumps({"scope_id": scope, "problem_key": key, "reason": reason}, ensure_ascii=False)
        self._conn.execute(
            "INSERT INTO feedback_actions(ticket_id, actor, action, note, at) VALUES (NULL, ?, ?, ?, ?)",
            (actor, action, note, at),
        )

    def pause(self, scope: str, key: str, actor: str, reason: str) -> None:
        """Pause a problem in one scope, replacing its reason and recording audit."""
        now = time.time()
        with self._feedback_transaction():
            self._conn.execute(
                """INSERT INTO paused_problems(scope_id, problem_key, reason, actor, at)
                    VALUES (?, ?, ?, ?, ?) ON CONFLICT(scope_id, problem_key)
                    DO UPDATE SET reason = excluded.reason, actor = excluded.actor, at = excluded.at""",
                (scope, key, reason, actor, now),
            )
            self._pause_action(scope, key, actor, "pause", reason, now)

    def unpause(self, scope: str, key: str, actor: str, reason: str = "") -> None:
        """Remove a scoped pause and record the request, including no-op requests."""
        with self._feedback_transaction():
            self._conn.execute(
                "DELETE FROM paused_problems WHERE scope_id = ? AND problem_key = ?", (scope, key),
            )
            self._pause_action(scope, key, actor, "unpause", reason, time.time())

    def paused_keys(self, scope: str) -> set[str]:
        """Return the problem keys currently paused in this scope."""
        return {row[0] for row in self._conn.execute(
            "SELECT problem_key FROM paused_problems WHERE scope_id = ?", (scope,),
        )}

    def link_pause_action(self, ticket_id: int, actor: str, action: str, note: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO feedback_actions(ticket_id,actor,action,note,at) VALUES(?,?,?,?,?)",
                (ticket_id, actor, action, note, time.time()),
            )

    def actions(self, scope: str, id: int) -> list[dict]:
        """Return a ticket's audit in chronological order only in its own scope."""
        rows = self._conn.execute(
            """SELECT a.* FROM feedback_actions AS a
                JOIN feedback_tickets AS t ON t.id = a.ticket_id
                WHERE t.scope_id = ? AND t.id = ? ORDER BY a.at, a.id""",
            (scope, id),
        ).fetchall()
        return [dict(row) for row in rows]
