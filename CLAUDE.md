# Claude Option Sync

The project is described in README.md. Python package: `src/claude_option/`. VS Code mod: `claude-mod/`. Run the tests with `.venv/bin/pytest`.

Rules for this repo:
- Never print, cat, grep or otherwise display `.env`, and never echo the Telegram bot token.
- The mod's source of truth is `claude-mod/`. After editing it, run `claude plugin validate claude-mod`.
- Paths in `.claude/settings.json` and `claude-mod/hooks/config.ts` are written by `scripts/install.sh` / `scripts/install.ps1`. Re-run the script; don't edit those paths by hand.
- The code must keep working on Linux, macOS and Windows. Put OS differences in `src/claude_option/utils/platform.py`. Open text files with `encoding="utf-8"`. The broker API is a Unix socket or 127.0.0.1 TCP; clients find out which by reading `data/run/endpoint.json`.

## Synchronized User Choice Workflow

When you need the user to choose between options, use the native `AskUserQuestion` tool. Don't write the options as plain text, and don't build your own approval mechanism. Each question is shown in the VS Code `question-sync` panel and sent to the paired Telegram chat at the same time. The first answer committed by the broker wins.

- Give every question 2–4 clear, distinct options with short labels and one-line descriptions. Keep question texts unique within a call, because answers are keyed by question text.
- Use `multiSelect: true` only when several options can be true at once.
- The answer comes back as the tool result. Treat it as the user's decision, whichever interface it came from.
- If the result says the question **expired** or was **cancelled**, no choice was made. Don't assume or pick an answer. Ask again only if the decision is still needed; otherwise stop and report.
- If the user gives a custom ("Other") answer, follow it even when it matches none of the options.
- Don't ask the user for Telegram chat IDs or user IDs. Pairing is done with `pair-code` and `/pair CODE`.
