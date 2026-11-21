#!/usr/bin/env bash
# Install / re-install Claude Option Sync on this machine. Safe to run repeatedly.
#
#   ./scripts/install.sh              venv + deps, .env check, mod config, project hook
#   ./scripts/install.sh --global     also add the PreToolUse hook to ~/.claude/settings.json
#                                     (every project on this machine, not just this one)
#   ./scripts/install.sh --mod        also install the VS Code mod (question-sync) as a
#                                     user-scope Claude Code plugin from ./claude-mod
#   ./scripts/install.sh --systemd    also install + enable a systemd --user service
#   ./scripts/install.sh --all        all of the above
#
# Never prints the contents of .env or the bot token.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GLOBAL=0; MOD=0; SYSTEMD=0
for a in "$@"; do
  case "$a" in
    --global) GLOBAL=1 ;; --mod) MOD=1 ;; --systemd) SYSTEMD=1 ;;
    --all) GLOBAL=1; MOD=1; SYSTEMD=1 ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
cd "$ROOT"
say() { printf '\n==> %s\n' "$*"; }

say "Python environment"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10+ is required (found: $("$PY" --version 2>&1))" >&2; exit 1; }
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q -e .
echo "ok: $(.venv/bin/python --version)"

say "Secrets (.env)"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Put your bot token in TELEGRAM_BOT_TOKEN, then re-run."
fi
chmod 600 .env
grep -qxF '.env' .gitignore 2>/dev/null || echo '.env' >> .gitignore
if grep -Eq '^TELEGRAM_BOT_TOKEN=.+' .env; then echo "ok: TELEGRAM_BOT_TOKEN is set (value not shown)"
else echo "WARNING: TELEGRAM_BOT_TOKEN is empty in .env"; fi

say "Runtime directories"
mkdir -p data/run && chmod 700 data data/run
echo "ok: $ROOT/data (0700)"

say "Mod configuration"
.venv/bin/python -m claude_option.cli mod-config

say "Claude Code hook (PreToolUse / AskUserQuestion)"
.venv/bin/python -m claude_option.cli install-hook --settings "$ROOT/.claude/settings.json"
if [[ $GLOBAL == 1 ]]; then
  [[ -f "$HOME/.claude/settings.json" ]] && cp "$HOME/.claude/settings.json" "$HOME/.claude/settings.json.bak-claude-option"
  .venv/bin/python -m claude_option.cli install-hook --settings "$HOME/.claude/settings.json"
fi

if [[ $MOD == 1 ]]; then
  say "VS Code mod (question-sync plugin)"
  if ! command -v claude >/dev/null; then
    echo "WARNING: 'claude' CLI not found on PATH; install the mod later with:"
    echo "  claude plugin install question-sync --marketplace \"$ROOT/claude-mod\" --scope user"
  else
    claude plugin validate "$ROOT/claude-mod" >/dev/null && echo "ok: mod validates"
    claude plugin install question-sync --marketplace "$ROOT/claude-mod" --scope user
  fi
fi

if [[ $SYSTEMD == 1 ]]; then
  say "systemd --user service"
  UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/claude-option.service" <<EOF
[Unit]
Description=Claude Option Sync broker + Telegram bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m claude_option.cli serve
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
EOF
  .venv/bin/python -m claude_option.cli stop >/dev/null 2>&1 || true
  systemctl --user daemon-reload
  systemctl --user enable --now claude-option.service
  echo "ok: systemctl --user status claude-option"
  echo "    (to keep it running while logged out: sudo loginctl enable-linger $USER)"
fi

say "Telegram bot check"
if grep -Eq '^TELEGRAM_BOT_TOKEN=.+' .env; then
  .venv/bin/python -m claude_option.cli check-bot \
    || echo "Fix expected_bot_username in config/settings.local.yaml (or remove it)."
fi

cat <<EOF

Done. Next:
  1. Start the broker:   ./scripts/start.sh        (skip if you used --systemd)
  2. Pair Telegram:      .venv/bin/python -m claude_option.cli pair-code
                         then send /pair CODE to your bot in a private chat
  3. Restart Claude Code sessions (or run /reload-plugins) so the hook and mod load.
EOF
