<#
.SYNOPSIS
  Remove Claude Option Sync from Claude Code on Windows (keeps the project folder, .env and data).
#>
$Root = Split-Path -Parent $PSScriptRoot
$VPy = Join-Path $Root '.venv\Scripts\python.exe'
& $VPy -m claude_option.cli stop
if (Get-ScheduledTask -TaskName 'ClaudeOptionSync' -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName 'ClaudeOptionSync' -Confirm:$false
  Write-Host 'ok: scheduled task removed'
}
& $VPy -m claude_option.cli uninstall-hook --settings (Join-Path $Root '.claude\settings.json')
& $VPy -m claude_option.cli uninstall-hook --settings (Join-Path $HOME '.claude\settings.json')
if (Get-Command claude -ErrorAction SilentlyContinue) {
  claude plugin uninstall question-sync
  claude plugin marketplace remove question-sync-local
}
