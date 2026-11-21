"""The authoritative question broker. All state lives in SQLite; this class is the only writer."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from ..utils.security import random_id, sha256_text
from .audit import audit
from .database import Database
from .events import ChangeBus
from .models import (ANSWERED, CANCELLED, EXPIRED, FINAL_STATES, PENDING, SOURCES, SelectResult,
                     ValidationError, canonical_json, validate_questions)
from .selection import commit_selection

log = logging.getLogger(__name__)


class QuestionBroker:
    def __init__(self, db: Database, *, timeout_seconds: int = 600, bus: ChangeBus | None = None):
        self.db = db
        self.timeout_seconds = timeout_seconds
        self.bus = bus or ChangeBus()

    # ------------------------------------------------------------------ creation

    def ensure_question(self, *, session_id: str, tool_use_id: str, questions: list,
                        project_root: str | None = None, agent_id: str | None = None,
                        source_tool: str = "AskUserQuestion", timeout_seconds: int | None = None) -> dict:
        """Idempotent: the same (session_id, tool_use_id) always maps to the same request."""
        if not session_id or not tool_use_id:
            raise ValidationError("session_id and tool_use_id are required")
        validate_questions(questions)
        key = sha256_text(f"{session_id}\0{tool_use_id}")
        qhash = sha256_text(canonical_json(questions))
        now = time.time()
        timeout = timeout_seconds or self.timeout_seconds
        project_name = os.path.basename(os.path.normpath(project_root)) if project_root else None
        created = False
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, project_name, project_root, created_at, last_seen_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET last_seen_at = excluded.last_seen_at,"
                " project_root = COALESCE(excluded.project_root, sessions.project_root),"
                " project_name = COALESCE(excluded.project_name, sessions.project_name)",
                (session_id, project_name, project_root, now, now),
            )
            row = conn.execute("SELECT request_id, question_hash FROM question_requests WHERE idempotency_key = ?",
                               (key,)).fetchone()
            if row is not None:
                if row["question_hash"] != qhash:
                    raise ValidationError("this tool call is already registered with different questions")
                request_id = row["request_id"]
            else:
                request_id = self._new_request_id(conn)
                conn.execute(
                    "INSERT INTO question_requests(request_id, idempotency_key, session_id, tool_use_id, agent_id,"
                    " question_hash, source_tool, original_input, created_at, expires_at, status)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (request_id, key, session_id, tool_use_id, agent_id, qhash, source_tool,
                     json.dumps({"questions": questions}, ensure_ascii=False), now, now + timeout, PENDING),
                )
                for qpos, q in enumerate(questions):
                    question_id = f"{request_id}-{qpos + 1}"
                    conn.execute(
                        "INSERT INTO question_items(question_id, request_id, position, question_text, header,"
                        " multi_select, status) VALUES (?,?,?,?,?,?,?)",
                        (question_id, request_id, qpos, q["question"], q.get("header"),
                         1 if q.get("multiSelect") else 0, PENDING),
                    )
                    for opos, o in enumerate(q["options"]):
                        conn.execute(
                            "INSERT INTO question_options(option_id, question_id, position, label, description)"
                            " VALUES (?,?,?,?,?)",
                            (self._new_option_id(conn), question_id, opos, o["label"], o.get("description")),
                        )
                audit(conn, "request_created", request_id=request_id, source="broker",
                      details={"session_id": session_id, "tool_use_id": tool_use_id, "questions": len(questions)})
                created = True
        if created:
            self.bus.publish()
        state = self.get_state(request_id)
        state["created"] = created
        return state

    def _new_request_id(self, conn) -> str:
        while True:
            rid = random_id(8)
            if conn.execute("SELECT 1 FROM question_requests WHERE request_id = ?", (rid,)).fetchone() is None:
                return rid

    def _new_option_id(self, conn) -> str:
        while True:
            oid = random_id(10)
            if conn.execute("SELECT 1 FROM question_options WHERE option_id = ?", (oid,)).fetchone() is None:
                return oid

    # ------------------------------------------------------------------ reads

    def find_request(self, session_id: str, tool_use_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT request_id FROM question_requests WHERE session_id = ? AND tool_use_id = ?",
                               (session_id, tool_use_id)).fetchone()
        return self.get_state(row["request_id"]) if row else None

    def get_state(self, request_id: str) -> dict:
        self.expire_due()
        with self.db.connect() as conn:
            r = conn.execute(
                "SELECT r.*, s.project_name, s.project_root FROM question_requests r"
                " JOIN sessions s USING (session_id) WHERE r.request_id = ?",
                (request_id,),
            ).fetchone()
            if r is None:
                raise KeyError(request_id)
            original = json.loads(r["original_input"])["questions"]
            items = conn.execute("SELECT * FROM question_items WHERE request_id = ? ORDER BY position",
                                 (request_id,)).fetchall()
            questions = []
            answers = {}
            for it in items:
                opts = conn.execute(
                    "SELECT option_id, label, description, position FROM question_options"
                    " WHERE question_id = ? ORDER BY position", (it["question_id"],)).fetchall()
                ans = conn.execute("SELECT * FROM question_answers WHERE question_id = ?",
                                   (it["question_id"],)).fetchone()
                answer = None
                if ans is not None:
                    answer = {
                        "option_ids": json.loads(ans["selected_option_ids"]),
                        "labels": json.loads(ans["selected_labels"]),
                        "text": ans["answer_text"],
                        "is_custom": bool(ans["is_custom"]),
                        "source": ans["selected_source"],
                        "selected_by": ans["selected_by"],
                        "resolved_at": ans["resolved_at"],
                    }
                    answers[it["question_text"]] = ans["answer_text"]
                questions.append({
                    "question_id": it["question_id"],
                    "position": it["position"],
                    "question": it["question_text"],
                    "header": it["header"],
                    "multi_select": bool(it["multi_select"]),
                    "status": it["status"],
                    "options": [dict(o) for o in opts],
                    "answer": answer,
                })
        return {
            "request_id": r["request_id"],
            "session_id": r["session_id"],
            "tool_use_id": r["tool_use_id"],
            "agent_id": r["agent_id"],
            "project_name": r["project_name"],
            "project_root": r["project_root"],
            "status": r["status"],
            "status_reason": r["status_reason"],
            "version": r["version"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "resolved_at": r["resolved_at"],
            "original_questions": original,
            "questions": questions,
            "answers": answers if r["status"] == ANSWERED else {},
        }

    def wait(self, request_id: str, since_version: int, timeout: float) -> dict:
        """Long-poll: returns as soon as the request's version moves past since_version."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            seq = self.bus.seq
            state = self.get_state(request_id)
            remaining = deadline - time.monotonic()
            if state["version"] > since_version or state["status"] in FINAL_STATES or remaining <= 0:
                return state
            # Wake on any change, and at least once a second to catch expiry.
            self.bus.wait_for_change(seq, min(remaining, 1.0))

    def pending_requests(self, limit: int = 20) -> list[dict]:
        self.expire_due()
        with self.db.connect() as conn:
            rows = conn.execute("SELECT request_id FROM question_requests WHERE status = ?"
                                " ORDER BY created_at LIMIT ?", (PENDING, limit)).fetchall()
        return [self.get_state(r["request_id"]) for r in rows]

    def recent_history(self, limit: int = 10) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT i.question_text, a.answer_text, a.selected_source, a.resolved_at, i.request_id"
                " FROM question_answers a JOIN question_items i USING (question_id)"
                " ORDER BY a.resolved_at DESC LIMIT ?", (limit,)).fetchall()
            closed = conn.execute(
                "SELECT request_id, status, status_reason, resolved_at FROM question_requests"
                " WHERE status IN (?,?) ORDER BY resolved_at DESC LIMIT ?", (EXPIRED, CANCELLED, limit)).fetchall()
        return [dict(r) for r in rows] + [dict(r) for r in closed]

    def counts(self) -> dict:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM question_requests GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------ writes

    def select(self, question_id: str, option_ids: list[str], *, source: str, selected_by: str | None = None,
               custom_text: str | None = None) -> SelectResult:
        if source not in SOURCES:
            raise ValidationError("unknown source")
        self.expire_due()
        with self.db.transaction() as conn:
            won, reason = commit_selection(conn, question_id, list(option_ids or []), source, selected_by, custom_text)
            request_id = conn.execute("SELECT request_id FROM question_items WHERE question_id = ?",
                                      (question_id,)).fetchone()["request_id"]
        if won:
            self.bus.publish()
        return SelectResult(won=won, reason=reason, state=self.get_state(request_id))

    def toggle_draft(self, question_id: str, option_id: str, source: str) -> list[str]:
        """Multi-select drafts are per interface; only Submit competes for the win."""
        with self.db.transaction() as conn:
            item = conn.execute("SELECT status, multi_select FROM question_items WHERE question_id = ?",
                                (question_id,)).fetchone()
            if item is None or not item["multi_select"]:
                raise ValidationError("not a multi-select question")
            if item["status"] != PENDING:
                return []
            if conn.execute("SELECT 1 FROM question_options WHERE option_id = ? AND question_id = ?",
                            (option_id, question_id)).fetchone() is None:
                raise ValidationError("option does not belong to this question")
            row = conn.execute("SELECT option_ids FROM question_drafts WHERE question_id = ? AND source = ?",
                               (question_id, source)).fetchone()
            current = json.loads(row["option_ids"]) if row else []
            current = [o for o in current if o != option_id] if option_id in current else current + [option_id]
            conn.execute(
                "INSERT INTO question_drafts(question_id, source, option_ids, updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(question_id, source) DO UPDATE SET option_ids = excluded.option_ids,"
                " updated_at = excluded.updated_at",
                (question_id, source, json.dumps(current), time.time()),
            )
        return current

    def get_draft(self, question_id: str, source: str) -> list[str]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT option_ids FROM question_drafts WHERE question_id = ? AND source = ?",
                               (question_id, source)).fetchone()
        return json.loads(row["option_ids"]) if row else []

    def cancel(self, request_id: str, reason: str, source: str = "broker") -> dict:
        changed = self._close(request_id, CANCELLED, reason, source)
        if changed:
            self.bus.publish()
        return self.get_state(request_id)

    def _close(self, request_id: str, status: str, reason: str, source: str) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE question_requests SET status = ?, status_reason = ?, resolved_at = ?, version = version + 1"
                " WHERE request_id = ? AND status = ?",
                (status, reason, time.time(), request_id, PENDING))
            if cur.rowcount != 1:
                return False
            conn.execute("UPDATE question_items SET status = ? WHERE request_id = ? AND status = ?",
                         (status, request_id, PENDING))
            audit(conn, f"request_{status.lower()}", request_id=request_id, source=source, details={"reason": reason})
        return True

    def expire_due(self) -> int:
        now = time.time()
        try:
            with self.db.connect() as conn:
                due = [r["request_id"] for r in conn.execute(
                    "SELECT request_id FROM question_requests WHERE status = ? AND expires_at <= ?", (PENDING, now))]
        except sqlite3.Error:
            return 0
        n = 0
        for rid in due:
            if self._close(rid, EXPIRED, "timeout", "broker"):
                n += 1
        if n:
            self.bus.publish()
        return n

    def cancel_session(self, session_id: str, reason: str) -> int:
        with self.db.connect() as conn:
            rows = [r["request_id"] for r in conn.execute(
                "SELECT request_id FROM question_requests WHERE session_id = ? AND status = ?", (session_id, PENDING))]
        n = sum(1 for rid in rows if self._close(rid, CANCELLED, reason, "broker"))
        if n:
            self.bus.publish()
        return n

    def recover_after_restart(self) -> None:
        """Pending requests survive a broker restart; only expired ones are closed."""
        self.expire_due()


def open_broker(db_path: Path, timeout_seconds: int = 600) -> QuestionBroker:
    return QuestionBroker(Database(db_path), timeout_seconds=timeout_seconds)
