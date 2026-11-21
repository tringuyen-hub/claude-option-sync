"""Command line: serve, start, stop, status, pair-code, owner-status, revoke-owner, check-bot,
install-hook, uninstall-hook, mod-config."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

from .broker.database import Database
from .config import load_settings
from .hooks.protocol import BrokerClient, BrokerUnavailable
from .telegram.pairing import create_pairing_code, get_owner, revoke_owner
from .utils import platform as plat
from .utils.security import ensure_private_dir


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
    client = BrokerClient.from_settings(settings)
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


def _health(settings) -> dict | None:
    try:
        return BrokerClient.from_settings(settings).health()
    except BrokerUnavailable:
        return None


def cmd_start(args, settings) -> int:
    health = _health(settings)
    if health:
        print(f"broker already running (pid {health.get('pid')})")
        return 0
    ensure_private_dir(settings.log_file.parent)
    argv = [plat.python_executable(windowless=True), "-m", "claude_option.cli", "serve"]
    if args.no_telegram:
        argv.append("--no-telegram")
    pid = plat.spawn_background(argv, settings.project_root, settings.log_file.with_name("broker.out.log"))
    deadline = time.time() + args.wait
    while time.time() < deadline:
        health = _health(settings)
        if health:
            print(f"broker started (pid {health.get('pid')}, {health.get('transport')}, "
                  f"telegram: {health.get('telegram')})")
            return 0
        if not plat.pid_alive(pid):
            break
        time.sleep(0.2)
    print("broker failed to start; see data/broker.log and data/broker.out.log", file=sys.stderr)
    return 1


def _locked_pid(settings) -> int | None:
    """The pid of a broker that holds the instance lock but does not answer (or is unreachable)."""
    if not settings.lock_path.exists():
        return None
    try:
        plat.lock_file(settings.lock_path).close()
        return None                      # nobody holds the lock: no broker is running
    except plat.LockHeld:
        try:
            return int(settings.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None


def cmd_stop(args, settings) -> int:
    health = _health(settings)
    if not health:
        pid = _locked_pid(settings)
        if pid is None:
            print("broker not running")
            return 0
        plat.terminate(pid)
        print(f"broker stopped (pid {pid}, did not answer its API)")
        return 0
    pid = int(health.get("pid") or 0)
    try:
        BrokerClient.from_settings(settings).request("POST", "/v1/shutdown", json={}, timeout=5)
    except BrokerUnavailable:
        pass
    deadline = time.time() + 10
    while time.time() < deadline and pid and plat.pid_alive(pid):
        time.sleep(0.2)
    if pid and plat.pid_alive(pid):
        plat.terminate(pid)
    print("broker stopped")
    return 0


HOOK_MODULE = "claude_option.hooks.pre_tool_use"


def _is_our_hook(h: dict) -> bool:
    return str(h.get("command", "")).replace("\\", "/").endswith("/scripts/run_hook.sh") or \
        HOOK_MODULE in (h.get("args") or []) or HOOK_MODULE in str(h.get("command", ""))


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _strip_hooks(data: dict) -> None:
    pre = (data.get("hooks") or {}).get("PreToolUse") or []
    for entry in pre:
        entry["hooks"] = [h for h in entry.get("hooks", []) if not _is_our_hook(h)]
    pre[:] = [e for e in pre if e.get("hooks")]


def hook_entry(settings, form: str) -> dict:
    """exec form: Claude Code runs python directly, no shell (Windows, paths with spaces).
    shell form: the bash wrapper scripts/run_hook.sh (Linux/macOS)."""
    if form == "exec":
        return {"type": "command", "command": plat.python_executable(), "args": ["-m", HOOK_MODULE], "timeout": 900}
    return {"type": "command", "command": str(settings.project_root / "scripts" / "run_hook.sh"), "timeout": 900}


def cmd_install_hook(args, settings) -> int:
    path = Path(args.settings).expanduser()
    data = _load_json(path)
    _strip_hooks(data)
    form = args.form or ("exec" if plat.IS_WINDOWS else "shell")
    data.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
        {"matcher": "AskUserQuestion", "hooks": [hook_entry(settings, form)]})
    _write_json(path, data)
    print(f"ok: {path} ({form} form)")
    return 0


def cmd_uninstall_hook(args, settings) -> int:
    path = Path(args.settings).expanduser()
    if not path.exists():
        print(f"nothing to do: {path} does not exist")
        return 0
    data = _load_json(path)
    _strip_hooks(data)
    if data.get("hooks", {}).get("PreToolUse") == []:
        del data["hooks"]["PreToolUse"]
    if data.get("hooks") == {}:
        del data["hooks"]
    _write_json(path, data)
    print(f"ok: removed the AskUserQuestion hook from {path}")
    return 0


def cmd_mod_config(args, settings) -> int:
    path = settings.project_root / "claude-mod" / "hooks" / "config.ts"
    run_dir = settings.run_dir.resolve().as_posix()          # forward slashes work on Windows too
    path.write_text("// Written by the installer: the broker's private run directory (absolute).\n"
                    f"export const RUN_DIR = {json.dumps(run_dir)}\n", encoding="utf-8")
    print(f"ok: {path.relative_to(settings.project_root).as_posix()} -> {run_dir}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="claude_option")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the broker and Telegram bot")
    s.add_argument("--no-telegram", action="store_true", help="broker only (for local tests)")
    sub.add_parser("pair-code", help="generate a one-time Telegram pairing code")
    sub.add_parser("owner-status", help="show the paired Telegram owner")
    r = sub.add_parser("revoke-owner", help="remove the paired Telegram owner")
    r.add_argument("--yes", action="store_true")
    st = sub.add_parser("start", help="start the broker in the background")
    st.add_argument("--no-telegram", action="store_true")
    st.add_argument("--wait", type=float, default=15, help="seconds to wait for it to come up")
    sub.add_parser("stop", help="stop the running broker")
    ih = sub.add_parser("install-hook", help="add the AskUserQuestion PreToolUse hook to a settings.json")
    ih.add_argument("--settings", required=True, help="path of the Claude Code settings.json to update")
    ih.add_argument("--form", choices=["exec", "shell"], help="default: exec on Windows, shell elsewhere")
    uh = sub.add_parser("uninstall-hook", help="remove the hook from a settings.json")
    uh.add_argument("--settings", required=True)
    sub.add_parser("mod-config", help="write claude-mod/hooks/config.ts for this machine")
    sub.add_parser("status", help="broker health")
    sub.add_parser("check-bot", help="validate the bot token with getMe")
    args = parser.parse_args(argv)
    settings = load_settings()
    return {
        "serve": cmd_serve, "pair-code": cmd_pair_code, "owner-status": cmd_owner_status,
        "revoke-owner": cmd_revoke_owner, "status": cmd_status, "check-bot": cmd_check_bot,
        "start": cmd_start, "stop": cmd_stop, "install-hook": cmd_install_hook,
        "uninstall-hook": cmd_uninstall_hook, "mod-config": cmd_mod_config,
    }[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
