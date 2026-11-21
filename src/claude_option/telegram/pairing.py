"""Owner pairing: a one-time code shown only locally, consumed atomically on /pair."""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..broker.audit import audit
from ..broker.database import Database
from ..utils.security import hash_code, new_pairing_code, normalize_code


@dataclass
class Owner:
    telegram_user_id: int
    telegram_chat_id: int
    username: str | None
    paired_at: float


def get_owner(db: Database) -> Owner | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM owner WHERE id = 1").fetchone()
    if row is None:
        return None
    return Owner(row["telegram_user_id"], row["telegram_chat_id"], row["username"], row["paired_at"])


def create_pairing_code(db: Database, ttl_seconds: int = 300) -> tuple[str, float]:
    """Invalidates earlier unused codes; stores only the hash of the new one."""
    code = new_pairing_code()
    now = time.time()
    with db.transaction() as conn:
        conn.execute("UPDATE pairing_codes SET status = 'REVOKED' WHERE status = 'ACTIVE'")
        conn.execute("INSERT INTO pairing_codes(code_hash, created_at, expires_at, status) VALUES (?,?,?, 'ACTIVE')",
                     (hash_code(code), now, now + ttl_seconds))
        audit(conn, "pairing_code_created", source="cli")
    return code, now + ttl_seconds


def attempt_pairing(db: Database, code: str, *, user_id: int, chat_id: int, chat_type: str,
                    username: str | None, max_attempts: int = 5) -> tuple[bool, str]:
    if chat_type != "private":
        return False, "Pairing is only accepted in a private chat with the bot."
    if not normalize_code(code):
        return False, "Usage: /pair CODE  (generate the code locally with `pair-code`)."
    now = time.time()
    with db.transaction() as conn:
        owner = conn.execute("SELECT telegram_user_id FROM owner WHERE id = 1").fetchone()
        if owner is not None:
            if owner["telegram_user_id"] == user_id:
                return False, "You are already paired as the owner."
            audit(conn, "pairing_refused_owner_exists", source="telegram", details={"user_id": user_id})
            return False, "This bot already has an owner."
        conn.execute("UPDATE pairing_codes SET status = 'EXPIRED' WHERE status = 'ACTIVE' AND expires_at <= ?", (now,))
        cur = conn.execute(
            "UPDATE pairing_codes SET status = 'USED', used_at = ?"
            " WHERE code_hash = ? AND status = 'ACTIVE' AND expires_at > ?",
            (now, hash_code(code), now))
        if cur.rowcount != 1:
            conn.execute("UPDATE pairing_codes SET attempts = attempts + 1 WHERE status = 'ACTIVE'")
            conn.execute("UPDATE pairing_codes SET status = 'REVOKED' WHERE status = 'ACTIVE' AND attempts >= ?",
                         (max_attempts,))
            audit(conn, "pairing_failed", source="telegram", details={"user_id": user_id})
            return False, "Invalid or expired pairing code."
        conn.execute("INSERT INTO owner(id, telegram_user_id, telegram_chat_id, username, paired_at)"
                     " VALUES (1,?,?,?,?)", (user_id, chat_id, username, now))
        conn.execute("UPDATE pairing_codes SET status = 'REVOKED' WHERE status = 'ACTIVE'")
        audit(conn, "owner_paired", source="telegram", details={"user_id": user_id, "chat_id": chat_id})
    return True, "Paired. Claude Code questions will now appear here."


def revoke_owner(db: Database) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM owner WHERE id = 1")
        conn.execute("UPDATE pairing_codes SET status = 'REVOKED' WHERE status = 'ACTIVE'")
        if cur.rowcount:
            audit(conn, "owner_revoked", source="cli")
    return bool(cur.rowcount)
