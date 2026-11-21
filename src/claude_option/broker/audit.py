"""Audit trail: every state transition is recorded inside the same transaction."""
from __future__ import annotations

import json
import time


def audit(conn, event_type: str, *, request_id=None, question_id=None, source=None, details=None) -> None:
    conn.execute(
        "INSERT INTO audit_events(request_id, question_id, event_type, source, created_at, details)"
        " VALUES (?,?,?,?,?,?)",
        (request_id, question_id, event_type, source, time.time(),
         json.dumps(details, ensure_ascii=False) if details is not None else None),
    )
