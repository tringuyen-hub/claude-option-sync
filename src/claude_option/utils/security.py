"""Small security helpers: ids, codes, hashing, private files."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from .platform import make_private

# Crockford-like alphabet without easily confused characters (0/O, 1/I/L, U).
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


def random_id(length: int = 8) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def new_pairing_code() -> str:
    raw = random_id(8)
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_code(code: str) -> str:
    return hashlib.sha256(("claude-option-pair:" + normalize_code(code)).encode()).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    make_private(path, is_dir=True)


def write_private_file(path: Path, content: str) -> None:
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    make_private(path, is_dir=False)


def load_or_create_api_token(path: Path) -> str:
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            make_private(path, is_dir=False)
            return token
    token = secrets.token_urlsafe(32)
    write_private_file(path, token)
    return token
