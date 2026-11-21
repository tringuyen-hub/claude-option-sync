#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/run/broker.pid"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  PID="$(cat "$PID_FILE")"; kill -TERM "$PID"
  for _ in $(seq 1 50); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
  echo "broker stopped"
else
  echo "broker not running"
fi
