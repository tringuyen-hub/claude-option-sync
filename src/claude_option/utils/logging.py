"""Logging setup with a filter that redacts the Telegram bot token."""
from __future__ import annotations

import logging
import re
from pathlib import Path

_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if _TOKEN_RE.search(msg):
            record.msg = _TOKEN_RE.sub("<redacted-token>", msg)
            record.args = ()
        return True


def setup_logging(log_file: Path | None = None, level: int = logging.INFO) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    for h in handlers:
        h.addFilter(RedactFilter())
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)
    # httpx logs full request URLs at INFO, which would include the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
