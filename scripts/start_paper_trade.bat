@echo off
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "D=%%i"

set "RUNTIME=%ROOT%\logs\runtime"
if not exist "%RUNTIME%" mkdir "%RUNTIME%"

set "LOG=%RUNTIME%\paper_trade_%D%.log"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$found = $false; foreach ($p in Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine }) { $cl = $p.CommandLine; if ($cl -like '*yahoo_kabu_watch.py*' -and $cl -like '*--paper-trade*') { $found = $true; break } }; if ($found) { exit 0 } else { exit 1 }"

if %errorlevel% equ 0 (
  echo [%date% %time%] Already running, skip>> "%LOG%"
  exit /b 0
)

echo [%date% %time%] Starting paper_trade>> "%LOG%"
start "paper_trade" /MIN cmd /c "cd /d \"%ROOT%\" && python yahoo_kabu_watch.py --paper-trade --paper-trade-force-start --replay-config configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json >>\"%LOG%\" 2>&1"
exit /b 0
