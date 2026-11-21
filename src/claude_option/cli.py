"""Command line: serve, pair-code, owner-status, revoke-owner, status, check-bot."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .broker.database import Database
from .config import load_settings
from .hooks.protocol import BrokerClient, BrokerUnavailable
from .telegram.pairing import create_pairing_code, get_owner, revoke_owner


def _ts(value) -> str:
    return dt.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S") if value else "?"


def cmd_serve(args, settings) -> int:
    from .service import AlreadyRunning, run_service
    from .utils.logging import setup_logging

    setup_logging(settings.log_file)
    try:
        return run_service(settings, with_telegram=not args.no_telegram)
    except AlreadyRunning as exc:
        print(f"claude-option: {exc}", file=sys.stderr)
        return 3


def cmd_pair_code(args, settings) -> int:
    db = Database(settings.database)
    owner = get_owner(db)
    if owner is not None:
        print("An owner is already paired. Run `revoke-owner` first to pair a different account.")
        return 1
    code, expires = create_pairing_code(db, settings.pairing_code_ttl_seconds)
    print("Pairing code (single use, valid until %s):\n\n    %s\n" % (_ts(expires), code))
    bot = "@" + settings.expected_bot_username if settings.expected_bot_username else "your bot"
    print("Send this to %s in a private chat:\n\n    /pair %s\n" % (bot, code))
    return 0


def cmd_owner_status(args, settings) -> int:
    owner = get_owner(Database(settings.database))
    if owner is None:
        print("No owner paired. Run `pair-code`, then send /pair CODE to the bot.")
        return 1
    print(f"Owner paired at {_ts(owner.paired_at)}")
    print(f"  Telegram user ID: {owner.telegram_user_id}")
    print(f"  Telegram chat ID: {owner.telegram_chat_id}")
    if owner.username:
        print(f"  Username: @{owner.username}")
    return 0


def cmd_revoke_owner(args, settings) -> int:
    if not args.yes:
        print("This removes the paired Telegram owner. Re-run with --yes to confirm.")
        return 1
    print("Owner revoked." if revoke_owner(Database(settings.database)) else "No owner was paired.")
    return 0


def cmd_status(args, settings) -> int:
    client = BrokerClient(settings.socket_path, settings.api_token_path)
    try:
        health = client.health()
    except BrokerUnavailable as exc:
        print(f"broker: not running ({exc})")
        return 1
    print(json.dumps(health, indent=2, default=str))
    return 0


def cmd_check_bot(args, settings) -> int:
    from .telegram.api import TelegramApi

    me = TelegramApi(settings.telegram_token()).get_me()
    expected = settings.expected_bot_username
    ok = not expected or me.get("username") == expected
    note = "OK" if ok else f"UNEXPECTED USERNAME (expected @{expected})"
    if ok and not expected:
        note += " (set expected_bot_username in config/settings.local.yaml to pin it)"
    print(f"getMe: @{me.get('username')} — {note}")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="claude_option")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the broker and Telegram bot")
    s.add_argument("--no-telegram", action="store_true", help="broker only (for local tests)")
    sub.add_parser("pair-code", help="generate a one-time Telegram pairing code")
    sub.add_parser("owner-status", help="show the paired Telegram owner")
    r = sub.add_parser("revoke-owner", help="remove the paired Telegram owner")
    r.add_argument("--yes", action="store_true")
    sub.add_parser("status", help="broker health")
    sub.add_parser("check-bot", help="validate the bot token with getMe")
    args = parser.parse_args(argv)
    settings = load_settings()
    return {
        "serve": cmd_serve, "pair-code": cmd_pair_code, "owner-status": cmd_owner_status,
        "revoke-owner": cmd_revoke_owner, "status": cmd_status, "check-bot": cmd_check_bot,
    }[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
