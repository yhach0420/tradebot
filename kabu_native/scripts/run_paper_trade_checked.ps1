# Phase687W8 — One-command Paper Trade checked runner (PowerShell launcher)
# Sets PYTHONPATH and delegates to python -m small_paper.paper_trade_checked_runner
# Preferred entry: cd <repo>; .\run_paper_trade_checked.bat

[CmdletBinding()]
param(
    [switch]$NoPause,
    [switch]$SkipPaper,
    [switch]$SkipW4s,
    [switch]$DemoPushE2E,
    [switch]$CommFaultE2E,
    [switch]$ReuseCapture,
    [int]$ReuseCapturePid = 0,
    [switch]$FullDayCert
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$NativeRoot = Resolve-Path (Join-Path $ScriptDir "..")
$RepoRoot = Resolve-Path (Join-Path $NativeRoot "..")

$env:PYTHONPATH = "$(Join-Path $NativeRoot 'src');$RepoRoot"
$env:PYTHONIOENCODING = "utf-8"
# Independent Market Ingress V2 (WS owner + Raw-first). Opt out: MARKET_INGRESS_V2=0
if (-not $env:MARKET_INGRESS_V2 -or $env:MARKET_INGRESS_V2.Trim().Length -eq 0) {
    $env:MARKET_INGRESS_V2 = "1"
}

# Ensure child processes inherit resolved paths even if user shell had empty PYTHONPATH
if (-not $env:PYTHONPATH -or $env:PYTHONPATH.Trim().Length -eq 0) {
    $env:PYTHONPATH = "$(Join-Path $NativeRoot 'src');$RepoRoot"
}

if ($DemoPushE2E) {
    $env:TRADEBOT_DEMO_PUSH_E2E = "1"
}
if ($CommFaultE2E) {
    $env:TRADEBOT_COMM_FAULT_E2E = "1"
}
if ($FullDayCert) {
    $env:TRADEBOT_CERTIFICATION_MODE = "1"
}

Set-Location $NativeRoot

$pyArgs = @(
    "-m", "small_paper.paper_trade_checked_runner",
    "--no-pause",
    "--repo-root", "$RepoRoot",
    "--native-root", "$NativeRoot",
    "--paper-bat", (Join-Path $RepoRoot "run_paper_trade.bat")
)

if ($SkipPaper) { $pyArgs += "--skip-paper" }
if ($SkipW4s) { $pyArgs += "--skip-w4s" }
if ($DemoPushE2E) { $pyArgs += "--demo-push-e2e" }
if ($CommFaultE2E) { $pyArgs += "--comm-fault-e2e" }
if ($ReuseCapture) {
    $pyArgs += "--reuse-capture"
    if ($ReuseCapturePid -gt 0) {
        $pyArgs += @("--reuse-capture-pid", "$ReuseCapturePid")
    }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[BLOCKED]"
    Write-Host "failed_step: python"
    Write-Host "exit_code: 1"
    Write-Host "reason: python not found on PATH"
    Write-Host "next_action: Install Python and ensure it is on PATH."
    if (-not $NoPause) { Read-Host "Press Enter to exit" | Out-Null }
    exit 1
}

& python @pyArgs
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 1 }

if (-not $NoPause) {
    Read-Host "Press Enter to exit" | Out-Null
}

exit $code
