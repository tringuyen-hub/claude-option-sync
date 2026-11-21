#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
.venv/bin/python -m claude_option.cli status
echo
.venv/bin/python -m claude_option.cli owner-status
