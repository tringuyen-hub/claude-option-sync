"""Telegram long polling. Exactly one poller runs, guarded by the service's lock file."""
from __future__ import annotations

import logging
import threading
import time

from ..broker.database import Database
from .api import TelegramApi, TelegramError
from .handlers import UpdateHandler

log = logging.getLogger(__name__)


class TelegramPoller:
    def __init__(self, api: TelegramApi, db: Database, handler: UpdateHandler, poll_timeout: int = 25):
        self.api = api
        self.db = db
        self.handler = handler
        self.poll_timeout = poll_timeout

    def _offset(self) -> int | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = 'telegram_offset'").fetchone()
        return int(row["value"]) if row else None

    def _save_offset(self, offset: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO kv(key, value) VALUES ('telegram_offset', ?)"
                         " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(offset),))

    def run_forever(self, stop: threading.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                updates = self.api.get_updates(self._offset(), self.poll_timeout)
                backoff = 1.0
            except TelegramError as exc:
                if exc.error_code == 409:
                    log.error("another getUpdates consumer is running for this bot (409); retrying")
                else:
                    log.warning("getUpdates failed: %s", exc.description)
                stop.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            for update in updates:
                # Advance the offset first: a poison update must not loop forever.
                self._save_offset(update["update_id"] + 1)
                try:
                    self.handler.handle(update)
                except Exception:
                    log.exception("failed to handle update %s", update.get("update_id"))
            if not updates:
                time.sleep(0.05)
