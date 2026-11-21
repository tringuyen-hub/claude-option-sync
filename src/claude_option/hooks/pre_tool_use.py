"""PreToolUse hook for the built-in AskUserQuestion tool.

Finds the synchronized request for (session_id, tool_use_id), waits until it is
final, and returns the broker's committed answers through `updatedInput`.
It never invents an answer: an expired or cancelled request is denied with a
reason, and anything unexpected falls back to Claude Code's native dialog.
"""
from __future__ import annotations

import json
import sys
import time

from ..config import load_settings
from .protocol import BrokerClient, BrokerUnavailable

TOOL = "AskUserQuestion"


def _log(settings, record: dict) -> None:
    try:
        path = settings.project_root / "data" / "hook.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), **record}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def allow_with_answers(tool_input: dict, state: dict) -> dict:
    updated = dict(tool_input)                       # keep every original field (metadata, annotations...)
    updated["questions"] = tool_input.get("questions", state["original_questions"])
    updated["answers"] = dict(state["answers"])      # broker-committed answers only
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"Answered via Claude Option Sync (request {state['request_id']})",
            "updatedInput": updated,
        }
    }


def deny_closed(state: dict) -> dict:
    if state["status"] == "EXPIRED":
        reason = ("The user did not select an option before the question expired "
                  f"(request {state['request_id']}). No answer was chosen; do not assume one. "
                  "Ask again if the decision is still needed.")
    else:
        reason = (f"The question was cancelled ({state.get('status_reason') or 'cancelled'}) before the user "
                  "answered. No answer was chosen; do not assume one.")
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def wait_final(client: BrokerClient, state: dict) -> dict:
    while state["status"] == "PENDING":
        state = client.wait(state["request_id"], state["version"], timeout=20)
    return state


def decide(event: dict, client: BrokerClient, standalone_mode: str = "native") -> dict | None:
    """Returns the hook's JSON output, or None to leave the call to Claude Code."""
    if event.get("tool_name") != TOOL:
        return None
    tool_input = event.get("tool_input") or {}
    session_id = event.get("session_id") or ""
    tool_use_id = event.get("tool_use_id") or ""
    if not session_id or not tool_use_id:
        return None
    state = client.lookup(session_id, tool_use_id)
    if state is None:
        if tool_input.get("answers") or standalone_mode != "telegram":
            return None
        status, state = client.ensure({"session_id": session_id, "tool_use_id": tool_use_id,
                                       "questions": tool_input.get("questions"), "project_root": event.get("cwd"),
                                       "agent_id": event.get("agent_id")})
        if status != 200:
            return None
    state = wait_final(client, state)
    if state["status"] == "ANSWERED":
        return allow_with_answers(tool_input, state)
    return deny_closed(state)


def main() -> int:
    settings = load_settings()
    try:
        # bytes, not text: on Windows sys.stdin decodes with the ANSI code page, not UTF-8
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    client = BrokerClient.from_settings(settings)
    record = {"tool": event.get("tool_name"), "session_id": event.get("session_id"),
              "tool_use_id": event.get("tool_use_id"), "had_answers": bool((event.get("tool_input") or {}).get("answers"))}
    try:
        out = decide(event, client, settings.hook_standalone_mode)
    except BrokerUnavailable as exc:
        _log(settings, {**record, "result": "broker_unavailable", "detail": str(exc)[:200]})
        print(f"claude-option: broker unavailable, using the native question dialog ({exc})", file=sys.stderr)
        return 0
    _log(settings, {**record, "result": "passthrough" if out is None else
                    out["hookSpecificOutput"]["permissionDecision"]})
    if out is not None:
        sys.stdout.write(json.dumps(out))   # ASCII-only JSON: safe under any console code page
    return 0


if __name__ == "__main__":
    sys.exit(main())
