#!/usr/bin/env bash
# PreToolUse hook entry point for AskUserQuestion. Never fails the tool call:
# any error falls back to Claude Code's native question dialog.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/.venv/bin/python" -m claude_option.hooks.pre_tool_use 2>>"$ROOT/data/hook.stderr.log"
