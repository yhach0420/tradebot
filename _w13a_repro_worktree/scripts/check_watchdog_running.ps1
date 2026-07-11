$ErrorActionPreference = 'Stop'
$log = $env:WATCHDOG_DUP_LOG
if (-not $log) {
    exit 1
}
try {
    $procs = @(
        Get-CimInstance -ClassName Win32_Process -Filter "name='python.exe'" |
            Where-Object { $_.CommandLine -and ($_.CommandLine -like '*watchdog.py*') }
    )
    if ($procs.Count -gt 0) {
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -LiteralPath $log -Encoding utf8 -Value "[$ts] start_watchdog: skip duplicate (watchdog.py already running)"
        foreach ($p in $procs) {
            Add-Content -LiteralPath $log -Encoding utf8 -Value "[$ts] PID=$($p.ProcessId) CommandLine=$($p.CommandLine)"
        }
        exit 0
    }
    exit 1
} catch {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $log -Encoding utf8 -Value "[$ts] check_watchdog_running.ps1 error: $($_.Exception.Message)"
    exit 1
}
