@echo off
setlocal EnableExtensions

rem Phase687W8 — One-command Paper Trade checked runner
rem Usage:
rem   cd C:\Users\yhach\Documents\tradebotfile
rem   .\run_paper_trade_checked.bat
rem Optional:
rem   .\run_paper_trade_checked.bat --no-pause

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
if /I "%~1"=="--no-pause" set "PSFLAGS=-NoPause"
if /I "%~1"=="/no-pause" set "PSFLAGS=-NoPause"

rem Process-scoped execution policy via -ExecutionPolicy Bypass (does not change machine policy)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %PSFLAGS%
exit /b %ERRORLEVEL%
