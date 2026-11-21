"""Configuration loading. Secrets come only from .env and are never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    project_root: Path
    database: Path
    run_dir: Path
    log_file: Path
    question_timeout_seconds: int = 600
    long_poll_max_seconds: int = 25
    telegram_poll_timeout: int = 25
    pairing_code_ttl_seconds: int = 300
    max_pair_attempts: int = 5
    expected_bot_username: str = ""     # optional guard against a wrong token
    hook_standalone_mode: str = "native"
    api_transport: str = "auto"         # auto (unix on POSIX, tcp on Windows) | unix | tcp
    api_port: int = 0                   # tcp only: 0 = a random free port on 127.0.0.1
    _token: str | None = field(default=None, repr=False)

    @property
    def socket_path(self) -> Path:
        return self.run_dir / "broker.sock"

    @property
    def endpoint_path(self) -> Path:
        """Written by the running broker: how to reach it (transport, socket or port)."""
        return self.run_dir / "endpoint.json"

    @property
    def transport(self) -> str:
        if self.api_transport in ("unix", "tcp"):
            return self.api_transport
        return "tcp" if os.name == "nt" else "unix"

    @property
    def api_token_path(self) -> Path:
        return self.run_dir / "api.token"

    @property
    def lock_path(self) -> Path:
        return self.run_dir / "broker.lock"

    @property
    def pid_path(self) -> Path:
        return self.run_dir / "broker.pid"

    def telegram_token(self) -> str:
        """The bot token from .env (or the environment). Never print this."""
        if self._token is None:
            values = dotenv_values(self.project_root / ".env")
            token = os.environ.get("TELEGRAM_BOT_TOKEN") or values.get("TELEGRAM_BOT_TOKEN") or ""
            self._token = token.strip()
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from .env")
        return self._token


def _merge(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def load_settings(root: Path | None = None, overrides: dict | None = None) -> Settings:
    root = Path(root or os.environ.get("CLAUDE_OPTION_ROOT") or PROJECT_ROOT)
    raw: dict = {}
    # settings.yaml is committed; settings.local.yaml (git-ignored) holds per-machine overrides.
    for name in ("settings.yaml", "settings.local.yaml"):
        cfg = root / "config" / name
        if cfg.exists():
            _merge(raw, yaml.safe_load(cfg.read_text(encoding="utf-8")) or {})
    broker = raw.get("broker", {}) or {}
    telegram = raw.get("telegram", {}) or {}
    hook = raw.get("hook", {}) or {}

    def p(value: str, default: str) -> Path:
        path = Path(value or default)
        return path if path.is_absolute() else root / path

    s = Settings(
        project_root=root,
        database=p(broker.get("database"), "data/claude_option.db"),
        run_dir=p(broker.get("run_dir"), "data/run"),
        log_file=p(broker.get("log_file"), "data/broker.log"),
        question_timeout_seconds=int(broker.get("question_timeout_seconds", 600)),
        long_poll_max_seconds=int(broker.get("long_poll_max_seconds", 25)),
        telegram_poll_timeout=int(telegram.get("poll_timeout_seconds", 25)),
        pairing_code_ttl_seconds=int(telegram.get("pairing_code_ttl_seconds", 300)),
        max_pair_attempts=int(telegram.get("max_pair_attempts", 5)),
        expected_bot_username=str(raw.get("expected_bot_username") or "").lstrip("@"),
        hook_standalone_mode=str(hook.get("standalone_mode", "native")),
        api_transport=str(broker.get("api_transport", "auto")),
        api_port=int(broker.get("api_port", 0)),
    )
    for key, value in (overrides or {}).items():
        setattr(s, key, value)
    return s
