"""The broker service: API socket + expiry + Telegram poller + Telegram sync, one process."""
from __future__ import annotations

import fcntl
import logging
import os
import signal
import threading
import time

from .broker.api import BrokerApiServer
from .broker.database import Database
from .broker.manager import QuestionBroker
from .config import Settings
from .telegram.api import TelegramApi, TelegramError
from .telegram.bot import TelegramPoller
from .telegram.handlers import COMMANDS, UpdateHandler
from .telegram.notifications import TelegramSync
from .telegram.pairing import get_owner
from .utils.security import ensure_private_dir, load_or_create_api_token, write_private_file

log = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    pass


def acquire_lock(settings: Settings):
    ensure_private_dir(settings.run_dir)
    fh = open(settings.lock_path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise AlreadyRunning("another broker instance is running") from None
    return fh


def run_service(settings: Settings, *, with_telegram: bool = True) -> int:
    lock = acquire_lock(settings)
    write_private_file(settings.pid_path, str(os.getpid()))
    token = load_or_create_api_token(settings.api_token_path)
    db = Database(settings.database)
    broker = QuestionBroker(db, timeout_seconds=settings.question_timeout_seconds)
    broker.recover_after_restart()
    stop = threading.Event()
    status = {"started_at": time.time(), "telegram": "disabled", "bot_username": None}

    def status_provider() -> dict:
        owner = get_owner(db)
        return {**status, "owner_paired": owner is not None, "counts": broker.counts(),
                "pid": os.getpid(), "timeout_seconds": settings.question_timeout_seconds}

    threads: list[threading.Thread] = []
    if with_telegram:
        api = TelegramApi(settings.telegram_token())
        me = api.get_me()
        if settings.expected_bot_username and me.get("username") != settings.expected_bot_username:
            raise RuntimeError(f"bot username is @{me.get('username')}, expected @{settings.expected_bot_username}")
        status.update(telegram="connected", bot_username=me["username"])
        try:
            api.set_commands(COMMANDS)
        except TelegramError as exc:
            log.warning("setMyCommands failed: %s", exc.description)
        sync = TelegramSync(broker, api)
        handler = UpdateHandler(broker, api, sync, max_pair_attempts=settings.max_pair_attempts,
                                status_provider=status_provider)
        poller = TelegramPoller(api, db, handler, settings.telegram_poll_timeout)
        threads.append(threading.Thread(target=poller.run_forever, args=(stop,), name="tg-poll", daemon=True))
        threads.append(threading.Thread(target=sync.run_forever, args=(stop,), name="tg-sync", daemon=True))
        log.info("Telegram bot @%s connected; owner paired: %s", me["username"], get_owner(db) is not None)

    server = BrokerApiServer(broker, settings.socket_path, token, status_provider, settings.long_poll_max_seconds)
    threads.append(threading.Thread(target=server.serve_forever, name="api", daemon=True))

    def expiry_loop():
        while not stop.wait(1.0):
            try:
                broker.expire_due()
            except Exception:
                log.exception("expiry failed")

    threads.append(threading.Thread(target=expiry_loop, name="expiry", daemon=True))

    def on_signal(signum, _frame):
        log.info("signal %s: stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    for t in threads:
        t.start()
    log.info("broker listening on %s", settings.socket_path)
    while not stop.wait(1.0):
        pass
    server.shutdown()
    try:
        settings.pid_path.unlink()
    except FileNotFoundError:
        pass
    lock.close()
    log.info("broker stopped")
    return 0
