#!/usr/bin/env bash
# Start the broker + Telegram bot in the background (single instance, lock-guarded).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data/run && chmod 700 data/run
if [[ -f data/run/broker.pid ]] && kill -0 "$(cat data/run/broker.pid)" 2>/dev/null; then
  echo "broker already running (pid $(cat data/run/broker.pid))"; exit 0
fi
nohup .venv/bin/python -m claude_option.cli serve "$@" >>data/broker.out.log 2>&1 &
for _ in $(seq 1 50); do
  [[ -S data/run/broker.sock ]] && .venv/bin/python -m claude_option.cli status >/dev/null 2>&1 && break
  sleep 0.2
done
if .venv/bin/python -m claude_option.cli status >/dev/null 2>&1; then
  echo "broker started (pid $(cat data/run/broker.pid))"
else
  echo "broker failed to start; see data/broker.log and data/broker.out.log" >&2; exit 1
fi
