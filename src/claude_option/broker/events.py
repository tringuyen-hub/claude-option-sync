"""In-process change notification. The database stays the source of truth; this only wakes waiters."""
from __future__ import annotations

import threading
import time


class ChangeBus:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = 0

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    def publish(self) -> None:
        with self._cond:
            self._seq += 1
            self._cond.notify_all()

    def wait_for_change(self, since_seq: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq <= since_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return self._seq
