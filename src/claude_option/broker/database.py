"""SQLite connection handling: WAL, foreign keys, busy timeout, explicit transactions."""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path

from ..utils.platform import make_private
from .migrations import migrate


class Database:
    """One connection per thread; all writes use BEGIN IMMEDIATE transactions."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            migrate(conn)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                make_private(p, is_dir=False)

    def _new_conn(self) -> sqlite3.Connection:
        # isolation_level=None: we issue BEGIN/COMMIT ourselves.
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextlib.contextmanager
    def connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
        yield conn

    @contextlib.contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE takes the write lock up front, so concurrent writers serialize."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
