# Claude Option Sync

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green)

When Claude Code asks you a question (`AskUserQuestion`), the same question and options appear in two places:

- **VS Code**: a `question-sync` panel inside the Claude Code chat (or a row in the transcript when the window is narrow)
- **Telegram**: a message from your bot, with one button per option

Answer in either place. **The first answer recorded wins.** The other interface turns read-only and shows the winning choice and where it came from ("Selected through Telegram" / "Selected in VS Code"). Claude continues with exactly that answer.

This is not a general approval bot. It only handles Claude's multiple-choice questions.

---

## 1. Daily use

| You want to… | Do this |
| --- | --- |
| Answer a question | Click an option in the VS Code panel **or** tap a button in Telegram |
| Answer a multi-select question | Tap options to toggle ☐/☑, then tap **Submit** |
| Give your own answer ("Other") | VS Code: type in the field under the options. Telegram: *reply* to the question message with your text |
| Reopen the panel | Type `/claude-option` in the Claude Code prompt |
| See open questions | Telegram: `/pending` |
| See recent answers | Telegram: `/history` |
| Check everything is up | Telegram: `/status`, or `./scripts/status.sh` |

If nobody answers within **10 minutes** (`question_timeout_seconds` in `config/settings.yaml`), the question expires. Both interfaces show "Expired" and Claude is told that no answer was chosen. Claude never makes up an answer.

If the broker is not running, Claude Code shows its normal built-in question dialog, so nothing breaks.

### Telegram commands

`/start` welcome · `/pair CODE` pair your account · `/help` · `/ping` · `/status` · `/pending` · `/history` · `/id`

Only the paired owner can use `/status`, `/pending`, `/history` and `/id`, and press question buttons. Messages from groups and from other accounts are ignored.

---

## 2. Install on a new PC

**Requirements:** Linux, macOS or Windows 10/11, Python 3.10 or newer, and Claude Code with the VS Code extension at version 2.1.304 or later (`claude --version`).

The steps below use Linux/macOS commands. **On Windows, follow [Install on Windows](#install-on-windows) instead.** It's the same process with PowerShell scripts.

### Step 1: Copy the project

Copy the project folder to the new PC with git, scp or a USB stick. **Do not copy** `.env`, `data/` or `.venv/`. They hold this PC's secrets, database and Python environment.

```bash
# example: copy from this PC without the private parts
rsync -a --exclude .env --exclude data --exclude .venv --exclude .pytest_cache \
  ./claude_option/  newpc:~/claude_option/
```

### Step 2: Create a bot for that PC

**One bot token can only be used by one running broker at a time.** Telegram lets only one program poll a bot. If two PCs share a token, both get `409 Conflict` errors and drop updates. So:

- **One PC** (moving the setup): stop the broker on the old PC (`./scripts/stop.sh`) and reuse the same bot.
- **Several PCs at once**: create one bot per PC. In Telegram, open **@BotFather**, send `/newbot` and follow the prompts. It gives you a token.

Write the token into `.env` on the new PC. Keep the file private and never paste the token into a chat.

```bash
cd ~/claude_option
cp .env.example .env
chmod 600 .env
nano .env          # set TELEGRAM_BOT_TOKEN=<token from BotFather>
```

Optional: pin the bot's username in `config/settings.local.yaml`. Git ignores this file, so each machine keeps its own:

```yaml
expected_bot_username: your_bot_name   # without the @
```

With it set, the broker refuses to start if `.env` holds a different bot's token. That guards against a wrong token.

### Step 3: Run the installer

```bash
./scripts/install.sh --all
```

`--all` does everything below. Use separate flags to pick only some of it.

| Flag | What it does |
| --- | --- |
| *(none)* | Creates `.venv`, installs dependencies, checks `.env` (without printing it), writes the mod's socket path, adds the hook to this project's `.claude/settings.json`, and runs `getMe` on the bot |
| `--global` | Also adds the hook to `~/.claude/settings.json`, so it works **in every project** on this PC. Your existing settings are kept and a backup is saved as `settings.json.bak-claude-option` |
| `--mod` | Installs the VS Code panel (`question-sync`) as a user plugin, so it loads **in every Claude Code session** |
| `--systemd` | Runs the broker as a `systemd --user` service that starts at login and restarts if it crashes |

The installer can be run again at any time. **Run it again whenever you move the folder**, because it writes absolute paths.

### Step 4: Start the broker

Skip this step if you used `--systemd`.

```bash
./scripts/start.sh
./scripts/status.sh      # expect "telegram": "connected"
```

### Step 5: Pair your Telegram account

Do this once per bot.

```bash
.venv/bin/python -m claude_option.cli pair-code
```

This prints a code such as `ABCD-EFGH`. It is valid for 5 minutes and works only once. In Telegram, open a **private chat** with your bot (search for its username or open `https://t.me/<bot_username>`), tap **Start**, then send:

```
/pair ABCD-EFGH
```

The bot replies to confirm. Your account is now the only owner. No one else can pair, and the owner can't be replaced, until you run `revoke-owner --yes` on the PC. You never have to look up or type a chat ID or user ID.

### Step 6: Restart Claude Code

Restart the Claude Code session (in VS Code: close the Claude tab and open a new one) so the hook and the plugin load. Then test it by asking Claude:

> Ask me which implementation to use: Python, C++ or Hybrid.

The question should appear in VS Code and in Telegram at the same time.

---

### Install on Windows

Everything works the same way on Windows. The only difference is how the VS Code panel and the hook reach the broker: Windows Python has no Unix sockets, so the broker listens on **127.0.0.1 only**, on a random port. Every request still needs the secret token, and requests from web browsers are refused.

1. **Install Python 3.10+** from [python.org](https://www.python.org/downloads/) and tick *Add python.exe to PATH*. Git for Windows is recommended for Claude Code but this project doesn't need it.
2. **Get the project** (PowerShell):
   ```powershell
   git clone https://github.com/tringuyen-hub/claude-option-sync.git $HOME\claude_option
   cd $HOME\claude_option
   git switch windows          # until this branch is merged into main
   Copy-Item .env.example .env
   notepad .env                # set TELEGRAM_BOT_TOKEN=<token from BotFather>
   ```
3. **Run the installer.** `-All` does everything; or pick `-Global`, `-Mod`, `-Autostart`:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -All
   ```
   | Flag | What it does |
   | --- | --- |
   | *(none)* | `.venv`, dependencies, `.env` and `data\` restricted to your user only (`icacls`), mod config, project hook |
   | `-Global` | Hook in `%USERPROFILE%\.claude\settings.json` for every project (backup saved) |
   | `-Mod` | VS Code panel installed as a user plugin |
   | `-Autostart` | Task Scheduler task `ClaudeOptionSync` starts the broker at logon, without a console window |
4. **Start it** (skip if you used `-Autostart`) **and pair**:
   ```powershell
   scripts\start.ps1
   scripts\status.ps1
   .venv\Scripts\python -m claude_option.cli pair-code      # then send /pair CODE to your bot
   ```
5. **Restart Claude Code** in VS Code.

On Windows the hook is written in Claude Code's *exec form* (`"command": "...\\python.exe", "args": ["-m", "claude_option.hooks.pre_tool_use"]`). It runs without a shell, so it works with or without Git Bash and with spaces in the path.

Windows commands: `scripts\start.ps1`, `scripts\stop.ps1`, `scripts\status.ps1`, `scripts\uninstall.ps1`. If PowerShell refuses to run scripts, prefix them with `powershell -ExecutionPolicy Bypass -File`.

---

## 3. Use it in another Claude Code session or project on the same PC

The broker serves every session on the PC. Each question is tied to its own session and tool call, so answers never cross between sessions. Each session needs two things:

1. **The hook.** It is already active in this project through `.claude/settings.json`. For other projects, run `./scripts/install.sh --global` once.
2. **The VS Code panel.** Run `./scripts/install.sh --mod` once. It installs the plugin for your user, so every new session loads it.

Without the plugin, VS Code shows only Claude's built-in dialog and the question does not go to Telegram. If you want a session to send questions to Telegram even without the panel, set `hook.standalone_mode: telegram` in `config/settings.yaml`.

To run the plugin for one session only, without installing it:

```bash
claude --plugin-dir ~/claude_option/claude-mod
```

---

## 4. Running it

```bash
./scripts/start.sh    # start broker + bot in the background
./scripts/status.sh   # health, Telegram connection, pairing
./scripts/stop.sh     # stop
```

With systemd:

```bash
systemctl --user status claude-option
systemctl --user restart claude-option
journalctl --user -u claude-option -f
sudo loginctl enable-linger $USER     # optional: keep running after logout
```

Other commands:

```bash
.venv/bin/python -m claude_option.cli owner-status     # who is paired
.venv/bin/python -m claude_option.cli revoke-owner --yes
.venv/bin/python -m claude_option.cli check-bot        # validate token with getMe
```

### Files

| Path | What |
| --- | --- |
| `.env` | Bot token (only your user can read it: mode 0600, or an `icacls` ACL on Windows; never committed, never printed) |
| `config/settings.yaml` | Shared defaults: timeout, hook mode |
| `config/settings.local.yaml` | Per-machine overrides, e.g. `expected_bot_username` (git-ignored) |
| `.claude/settings.json`, `claude-mod/hooks/config.ts` | Written by `install.sh` with this machine's absolute paths (git-ignored) |
| `data/claude_option.db` | SQLite database: questions, answers, pairing, audit trail |
| `data/run/api.token`, `data/run/endpoint.json` | Local API secret, and how to reach the broker: a Unix socket (`broker.sock`, Linux/macOS) or `127.0.0.1:<port>` (Windows). Private to your user; nothing listens on a network interface |
| `data/broker.log`, `data/hook.log` | Logs (the token is redacted) |
| `claude-mod/` | The VS Code panel plugin (`question-sync`) |

---

## 5. Troubleshooting

| Symptom | Check |
| --- | --- |
| Nothing arrives in Telegram | `./scripts/status.sh`: is `owner_paired` true and `telegram` "connected"? If not paired, do Step 5 |
| `409 Conflict` in `data/broker.log` | The same bot token is running on another PC or in another process. Stop it, or use a separate bot (Step 2) |
| `bot username is @x, expected @y` | Fix or remove `expected_bot_username` in `config/settings.local.yaml` |
| No panel in VS Code, only the built-in dialog | Is the plugin installed (`claude plugin list`)? Restart the session. Is the broker running? |
| Panel appears but Claude doesn't continue | Check the last lines of `data/hook.log` and `data/hook.stderr.log` |
| Pairing code rejected | It expired (5 min), was already used, or there were 5 wrong tries. Run `pair-code` again |
| Moved the folder | Run `./scripts/install.sh` (Windows: `scripts\install.ps1`) again, with the same flags you used before |
| Windows: "running scripts is disabled" | Run `powershell -ExecutionPolicy Bypass -File scripts\install.ps1` |
| Windows: no panel, hook says broker unavailable | `scripts\status.ps1`; if a firewall prompt appeared, allow *Private* only (the broker only uses 127.0.0.1) |

---

## 6. Uninstall

```bash
./scripts/stop.sh
systemctl --user disable --now claude-option 2>/dev/null; rm -f ~/.config/systemd/user/claude-option.service
claude plugin uninstall question-sync
claude plugin marketplace remove question-sync-local
```

Then remove the hook: `.venv/bin/python -m claude_option.cli uninstall-hook --settings .claude/settings.json` (and `--settings ~/.claude/settings.json` if you used `--global`).

On Windows, `scripts\uninstall.ps1` does all of this, including removing the scheduled task.

---

## 7. How it works

```
Claude calls AskUserQuestion
        │
        ▼
question-sync mod (VS Code) ──ensure──► broker (Python, SQLite) ──► Telegram bot ──► your phone
   shows panel, waits ◄──────wait────── first answer wins ◄── button tap / reply
        │                              (atomic compare-and-set)
        ▼
PreToolUse hook ── reads the committed answer from the broker ──► Claude continues
```

- The broker writes an answer only if the question is still open. That single atomic SQLite update guarantees exactly one winner, even when VS Code and Telegram are pressed at the same moment. The tests race 32 threads and 8 separate processes.
- The hook always passes Claude the broker's stored answer, never an answer that only came in with the tool call. It keeps the original questions as they were.
- Telegram button presses are checked against the paired user, the paired chat and the exact message the button belongs to before they count.

Development: `.venv/bin/pytest` runs the test suite.

---

## License

[MIT](LICENSE) © 2026 Tri Nguyen
