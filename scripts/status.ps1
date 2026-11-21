# Broker health, Telegram connection and pairing.
$Root = Split-Path -Parent $PSScriptRoot
$VPy = Join-Path $Root '.venv\Scripts\python.exe'
& $VPy -m claude_option.cli status
Write-Host ""
& $VPy -m claude_option.cli owner-status
