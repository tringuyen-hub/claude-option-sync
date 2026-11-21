"""The atomic first-writer-wins commit.

The winner is decided only by which BEGIN IMMEDIATE transaction performs the
conditional UPDATE first. No client timestamp is consulted.
"""
from __future__ import annotations

import json
import time

from .audit import audit
from .models import ANSWERED, PENDING, ValidationError


def commit_selection(conn, question_id: str, option_ids: list[str], source: str,
                     selected_by: str | None, custom_text: str | None, now: float | None = None) -> tuple[bool, str]:
    """Runs inside an open write transaction. Returns (won, reason)."""
    now = time.time() if now is None else now
    item = conn.execute(
        "SELECT i.question_id, i.request_id, i.multi_select, i.status AS item_status,"
        " r.status AS request_status, r.expires_at"
        " FROM question_items i JOIN question_requests r USING (request_id)"
        " WHERE i.question_id = ?",
        (question_id,),
    ).fetchone()
    if item is None:
        raise ValidationError("unknown question")

    options = {
        row["option_id"]: row
        for row in conn.execute(
            "SELECT option_id, label, position FROM question_options WHERE question_id = ?", (question_id,)
        )
    }
    custom = (custom_text or "").strip()
    if custom:
        if option_ids:
            raise ValidationError("give either options or custom text, not both")
        labels: list[str] = []
        answer_text = custom
    else:
        if not option_ids:
            raise ValidationError("no option selected")
        if len(set(option_ids)) != len(option_ids):
            raise ValidationError("duplicate options")
        for oid in option_ids:
            if oid not in options:
                raise ValidationError("option does not belong to this question")
        if not item["multi_select"] and len(option_ids) != 1:
            raise ValidationError("single-select question takes exactly one option")
        # Keep the original option order regardless of click order.
        option_ids = sorted(option_ids, key=lambda oid: options[oid]["position"])
        labels = [options[oid]["label"] for oid in option_ids]
        answer_text = ", ".join(labels)

    # Compare-and-set: only a PENDING item of a PENDING, unexpired request can be answered.
    cur = conn.execute(
        "UPDATE question_items SET status = ?"
        " WHERE question_id = ? AND status = ?"
        " AND EXISTS (SELECT 1 FROM question_requests r"
        "             WHERE r.request_id = question_items.request_id"
        "             AND r.status = ? AND r.expires_at > ?)",
        (ANSWERED, question_id, PENDING, PENDING, now),
    )
    if cur.rowcount != 1:
        if item["item_status"] == ANSWERED:
            reason = "already_answered"
        elif item["request_status"] != PENDING:
            reason = item["request_status"].lower()
        elif item["expires_at"] <= now:
            reason = "expired"
        else:
            reason = "already_answered"
        audit(conn, "selection_rejected", request_id=item["request_id"], question_id=question_id,
              source=source, details={"reason": reason, "option_ids": option_ids})
        return False, reason

    conn.execute(
        "INSERT INTO question_answers(question_id, selected_option_ids, selected_labels, answer_text,"
        " is_custom, selected_by, selected_source, resolved_at) VALUES (?,?,?,?,?,?,?,?)",
        (question_id, json.dumps(option_ids), json.dumps(labels, ensure_ascii=False), answer_text,
         1 if custom else 0, selected_by, source, now),
    )
    remaining = conn.execute(
        "SELECT COUNT(*) FROM question_items WHERE request_id = ? AND status = ?",
        (item["request_id"], PENDING),
    ).fetchone()[0]
    if remaining == 0:
        conn.execute(
            "UPDATE question_requests SET status = ?, resolved_at = ?, version = version + 1"
            " WHERE request_id = ?",
            (ANSWERED, now, item["request_id"]),
        )
    else:
        conn.execute("UPDATE question_requests SET version = version + 1 WHERE request_id = ?",
                     (item["request_id"],))
    conn.execute("DELETE FROM question_drafts WHERE question_id = ?", (question_id,))
    audit(conn, "selection_committed", request_id=item["request_id"], question_id=question_id,
          source=source, details={"option_ids": option_ids, "labels": labels, "custom": bool(custom)})
    return True, "won"
