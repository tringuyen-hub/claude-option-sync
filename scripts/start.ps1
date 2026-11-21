# Start the broker + Telegram bot in the background (single instance, lock-guarded).
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root '.venv\Scripts\python.exe') -m claude_option.cli start @args
exit $LASTEXITCODE
