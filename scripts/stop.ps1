# Stop the broker: asks it to shut down cleanly, then terminates it if it does not.
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root '.venv\Scripts\python.exe') -m claude_option.cli stop
exit $LASTEXITCODE
