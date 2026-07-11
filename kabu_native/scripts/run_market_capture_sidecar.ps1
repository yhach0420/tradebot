# Phase687W9 — Independent Market Capture Sidecar launcher (supervised, max 1 restart)
# Separate process from Paper. Does not enable live orders.
param(
    [string]$NativeRoot = "",
    [string]$TradingDate = "",
    [switch]$Synthetic,
    [int]$SyntheticEvents = 100
)

$ErrorActionPreference = "Stop"

if (-not $NativeRoot) {
    $NativeRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = Split-Path -Parent $NativeRoot
$env:PYTHONPATH = "$NativeRoot\src;$RepoRoot"
$env:PYTHONIOENCODING = "utf-8"

if (-not $TradingDate) {
    $TradingDate = python -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y%m%d'))"
}

$pyArgs = @(
    "-m", "small_paper.market_capture_supervisor",
    "--native-root", $NativeRoot,
    "--trading-date", $TradingDate
)
if ($Synthetic) {
    $pyArgs += @("--synthetic", "--synthetic-events", "$SyntheticEvents")
}

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "python"
$psi.Arguments = ($pyArgs -join " ")
$psi.WorkingDirectory = $NativeRoot
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$p = [System.Diagnostics.Process]::Start($psi)
Write-Output "CAPTURE_SIDECAR_SUPERVISOR_PID=$($p.Id)"
