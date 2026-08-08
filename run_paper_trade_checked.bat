@echo off
setlocal EnableExtensions

rem Phase687W8 — One-command Paper Trade checked runner
rem Usage:
rem   cd C:\Users\yhach\Documents\tradebotfile
rem   .\run_paper_trade_checked.bat
rem Optional:
rem   .\run_paper_trade_checked.bat --no-pause
rem   .\run_paper_trade_checked.bat --demo-push-e2e --no-pause
rem   .\run_paper_trade_checked.bat --comm-fault-e2e --no-pause
rem   .\run_paper_trade_checked.bat --reuse-capture --reuse-capture-pid 30100 --no-pause

set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

set "PS1=%REPO%\kabu_native\scripts\run_paper_trade_checked.ps1"
if not exist "%PS1%" (
  echo [BLOCKED]
  echo failed_step: launcher
  echo exit_code: 1
  echo reason: missing %PS1%
  echo next_action: Restore kabu_native\scripts\run_paper_trade_checked.ps1
  pause
  exit /b 1
)

set "PSFLAGS="
set "DEMO="
set "COMMFAULT="
set "REUSE="
set "REUSEPID="

:parse_args
if "%~1"=="" goto run_ps
if /I "%~1"=="--no-pause" set "PSFLAGS=%PSFLAGS% -NoPause"
if /I "%~1"=="/no-pause" set "PSFLAGS=%PSFLAGS% -NoPause"
if /I "%~1"=="--demo-push-e2e" (
  set "DEMO=-DemoPushE2E"
  set "TRADEBOT_DEMO_PUSH_E2E=1"
)
if /I "%~1"=="/demo-push-e2e" (
  set "DEMO=-DemoPushE2E"
  set "TRADEBOT_DEMO_PUSH_E2E=1"
)
if /I "%~1"=="--comm-fault-e2e" (
  set "COMMFAULT=-CommFaultE2E"
  set "TRADEBOT_COMM_FAULT_E2E=1"
)
if /I "%~1"=="/comm-fault-e2e" (
  set "COMMFAULT=-CommFaultE2E"
  set "TRADEBOT_COMM_FAULT_E2E=1"
)
if /I "%~1"=="--reuse-capture" set "REUSE=-ReuseCapture"
if /I "%~1"=="/reuse-capture" set "REUSE=-ReuseCapture"
if /I "%~1"=="--reuse-capture-pid" (
  set "REUSEPID=-ReuseCapturePid %~2"
  shift
)
if /I "%~1"=="/reuse-capture-pid" (
  set "REUSEPID=-ReuseCapturePid %~2"
  shift
)
shift
goto parse_args

:run_ps
rem Process-scoped execution policy via -ExecutionPolicy Bypass (does not change machine policy)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %PSFLAGS% %DEMO% %COMMFAULT% %REUSE% %REUSEPID%
exit /b %ERRORLEVEL%