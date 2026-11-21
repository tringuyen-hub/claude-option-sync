#!/usr/bin/env bash
# Stop the broker: asks it to shut down cleanly, then terminates it if it does not.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec .venv/bin/python -m claude_option.cli stop
