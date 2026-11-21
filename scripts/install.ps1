<#
.SYNOPSIS
  Install / re-install Claude Option Sync on Windows. Safe to run repeatedly.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1            # venv, deps, .env check, mod config, project hook
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Global    # also hook every project (~\.claude\settings.json)
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Mod       # also install the VS Code mod as a user plugin
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Autostart # also start the broker at logon (Task Scheduler)
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -All

  Never prints the contents of .env or the bot token.
#>
param([switch]$Global, [switch]$Mod, [switch]$Autostart, [switch]$All)

# Not 'Stop': in Windows PowerShell 5.1 that turns any native stderr line (pip notices) into a fatal
# error when output is redirected. Native commands are checked by exit code (Run); cmdlets use -ErrorAction Stop.
$ErrorActionPreference = 'Continue'
if ($All) { $Global = $true; $Mod = $true; $Autostart = $true }
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$TaskName = 'ClaudeOptionSync'

function Say($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Run {
  # Run a native command; stop on a non-zero exit code (ErrorActionPreference does not cover those).
  # A plain function on purpose: $args passes '-m', '--settings' etc. through untouched.
  $exe = $args[0]
  $rest = @($args | Select-Object -Skip 1)
  & $exe @rest
  if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $exe $($rest -join ' ')" }
}
function Make-Private($Path) {
  # Remove inherited permissions; grant only the current user full control.
  $who = "$env:USERDOMAIN\$env:USERNAME"
  $grant = if ((Get-Item $Path).PSIsContainer) { "${who}:(OI)(CI)F" } else { "${who}:F" }
  icacls $Path /inheritance:r /grant:r $grant | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Warning "could not restrict permissions on $Path" }
}

Say 'Python environment'
$py = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $py = 'py'; $pyArgs = @('-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = 'python' }
else { throw 'Python 3.10+ is required: install it from https://www.python.org/downloads/ (tick "Add to PATH").' }
& $py @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required.' }
$VPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $VPy)) { Run $py @pyArgs -m venv .venv }
Run $VPy -m pip install -q --upgrade pip
Run $VPy -m pip install -q -r requirements.txt
Run $VPy -m pip install -q -e .
Write-Host "ok: $(& $VPy --version)"

Say 'Secrets (.env)'
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env -ErrorAction Stop
  Write-Host 'Created .env from .env.example. Put your bot token in TELEGRAM_BOT_TOKEN, then re-run.'
}
Make-Private (Join-Path $Root '.env')
if (Select-String -Path .env -Pattern '^TELEGRAM_BOT_TOKEN=.+' -Quiet) { Write-Host 'ok: TELEGRAM_BOT_TOKEN is set (value not shown)' }
else { Write-Warning 'TELEGRAM_BOT_TOKEN is empty in .env' }

Say 'Runtime directories'
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data\run') -ErrorAction Stop | Out-Null
Make-Private (Join-Path $Root 'data')
Write-Host "ok: $Root\data (current user only)"

Say 'Mod configuration'
Run $VPy -m claude_option.cli mod-config

Say 'Claude Code hook (PreToolUse / AskUserQuestion)'
Run $VPy -m claude_option.cli install-hook --settings (Join-Path $Root '.claude\settings.json')
if ($Global) {
  $userSettings = Join-Path $HOME '.claude\settings.json'
  if (Test-Path $userSettings) { Copy-Item $userSettings "$userSettings.bak-claude-option" -Force }
  Run $VPy -m claude_option.cli install-hook --settings $userSettings
}

if ($Mod) {
  Say 'VS Code mod (question-sync plugin)'
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Warning "'claude' CLI not found on PATH; install the mod later with:"
    Write-Host "  claude plugin install question-sync --marketplace `"$Root\claude-mod`" --scope user"
  } else {
    Run claude plugin validate (Join-Path $Root 'claude-mod')
    Run claude plugin install question-sync --marketplace (Join-Path $Root 'claude-mod') --scope user
  }
}

if ($Autostart) {
  Say 'Start at logon (Task Scheduler)'
  & $VPy -m claude_option.cli stop | Out-Null
  $pyw = Join-Path $Root '.venv\Scripts\pythonw.exe'
  $action = New-ScheduledTaskAction -Execute $pyw -Argument '-m claude_option.cli serve' -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Claude Option Sync broker + Telegram bot' -Force -ErrorAction Stop | Out-Null
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "ok: scheduled task '$TaskName' registered and started"
}

Say 'Telegram bot check'
if (Select-String -Path .env -Pattern '^TELEGRAM_BOT_TOKEN=.+' -Quiet) {
  & $VPy -m claude_option.cli check-bot
  if ($LASTEXITCODE -ne 0) { Write-Host 'Fix expected_bot_username in config\settings.local.yaml (or remove it).' }
}

Write-Host @"

Done. Next:
  1. Start the broker:   scripts\start.ps1          (skip if you used -Autostart)
  2. Pair Telegram:      .venv\Scripts\python -m claude_option.cli pair-code
                         then send /pair CODE to your bot in a private chat
  3. Restart Claude Code sessions (or run /reload-plugins) so the hook and mod load.
"@
