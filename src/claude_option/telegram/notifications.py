"""Keeps Telegram messages in step with broker state: sends new questions, edits resolved ones."""
from __future__ import annotations

import logging
import threading
import time

from ..broker.manager import QuestionBroker
from .api import TelegramApi, TelegramError
from .keyboards import fingerprint, render_question
from .pairing import get_owner

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


class TelegramSync:
    def __init__(self, broker: QuestionBroker, api: TelegramApi):
        self.broker = broker
        self.db = broker.db
        self.api = api
        self._lock = threading.Lock()   # one sync pass at a time (worker + callback handler)

    # -- public

    def run_forever(self, stop: threading.Event) -> None:
        seq = 0
        while not stop.is_set():
            try:
                self.sync_all()
            except Exception:
                log.exception("telegram sync failed")
            seq = self.broker.bus.wait_for_change(seq, 2.0)

    def sync_all(self) -> None:
        owner = get_owner(self.db)
        if owner is None:
            return
        with self._lock:
            for request_id in self._requests_needing_sync():
                self._sync_request(request_id, owner.telegram_chat_id)

    def sync_question(self, question_id: str) -> None:
        owner = get_owner(self.db)
        if owner is None:
            return
        with self.db.connect() as conn:
            row = conn.execute("SELECT request_id FROM question_items WHERE question_id = ?", (question_id,)).fetchone()
        if row:
            with self._lock:
                self._sync_request(row["request_id"], owner.telegram_chat_id)

    # -- internals

    def _requests_needing_sync(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT r.request_id FROM question_requests r"
                " JOIN question_items i USING (request_id)"
                " LEFT JOIN telegram_messages m USING (question_id)"
                " WHERE (r.status = 'PENDING' AND (m.question_id IS NULL OR m.delivery_status IN ('FAILED','SENDING')))"
                "    OR (m.delivery_status IN ('SENT') AND (i.status != 'PENDING' OR r.status != 'PENDING'))"
                "    OR (m.delivery_status = 'SENT' AND r.status = 'PENDING')"
                " ORDER BY r.created_at").fetchall()
        return [r["request_id"] for r in rows]

    def _sync_request(self, request_id: str, chat_id: int) -> None:
        state = self.broker.get_state(request_id)
        for q in state["questions"]:
            with self.db.connect() as conn:
                msg = conn.execute("SELECT * FROM telegram_messages WHERE question_id = ?", (q["question_id"],)).fetchone()
            draft = self.broker.get_draft(q["question_id"], "telegram") if q["status"] == "PENDING" else []
            text, markup = render_question(state, q, draft)
            fp = fingerprint(text, markup)
            final = markup is None
            if msg is None or msg["telegram_message_id"] is None:
                if final:
                    continue   # never announce an already-closed question
                if msg is not None and msg["attempts"] >= MAX_ATTEMPTS:
                    continue
                self._send(q["question_id"], chat_id, text, markup, fp)
            elif msg["delivery_status"] != "FINAL" and msg["rendered_state"] != fp:
                self._edit(q["question_id"], msg["telegram_chat_id"], msg["telegram_message_id"], text, markup, fp, final)

    def _send(self, question_id: str, chat_id: int, text: str, markup: dict | None, fp: str) -> None:
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO telegram_messages(question_id, telegram_chat_id, delivery_status, attempts, updated_at)"
                " VALUES (?,?, 'SENDING', 1, ?) ON CONFLICT(question_id) DO UPDATE SET"
                " attempts = attempts + 1, delivery_status = 'SENDING', updated_at = excluded.updated_at",
                (question_id, chat_id, now))
        try:
            sent = self.api.send_message(chat_id, text, markup)
        except TelegramError as exc:
            log.warning("sendMessage failed for %s: %s", question_id, exc.description)
            with self.db.transaction() as conn:
                conn.execute("UPDATE telegram_messages SET delivery_status = 'FAILED', updated_at = ? WHERE question_id = ?",
                             (time.time(), question_id))
            return
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE telegram_messages SET telegram_message_id = ?, delivery_status = 'SENT', rendered_state = ?,"
                " updated_at = ? WHERE question_id = ?", (sent["message_id"], fp, time.time(), question_id))
        log.info("sent question %s to Telegram (message %s)", question_id, sent["message_id"])

    def _edit(self, question_id: str, chat_id: int, message_id: int, text: str, markup: dict | None,
              fp: str, final: bool) -> None:
        try:
            self.api.edit_message(chat_id, message_id, text, markup)
        except TelegramError as exc:
            log.warning("editMessageText failed for %s: %s", question_id, exc.description)
            if exc.error_code == 400 and "not found" in exc.description:
                final = True   # message deleted by the user: nothing left to update
            else:
                return
        with self.db.transaction() as conn:
            conn.execute("UPDATE telegram_messages SET rendered_state = ?, delivery_status = ?, updated_at = ?"
                         " WHERE question_id = ?", (fp, "FINAL" if final else "SENT", time.time(), question_id))
        if final:
            log.info("finalized Telegram message for %s", question_id)
