#!/usr/bin/env bash
# Start the broker + Telegram bot in the background (single instance, lock-guarded).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec .venv/bin/python -m claude_option.cli start "$@"
