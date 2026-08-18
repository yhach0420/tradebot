@echo off
setlocal EnableExtensions

rem Temporary OPVAL one-BAT launcher (outside Candidate-6 pinned runtime bytes).
rem Final certified launcher remains run_paper_trade_checked.bat.
rem Usage:
rem   cd C:\Users\yhach\Documents\tradebotfile
rem   .\run_paper_trade_opval.bat
rem   .\run_paper_trade_opval.bat --no-pause
rem   .\run_paper_trade_opval.bat --self-test --no-pause
rem   .\run_paper_trade_opval.bat --dry-run --no-pause

set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

set "NATIVE=%REPO%\kabu_native"
set "PY=%NATIVE%\scripts\run_paper_trade_opval.py"
if not exist "%PY%" (
  echo [BLOCKED]
  echo failed_step: launcher
  echo exit_code: 1
  echo reason: missing %PY%
  echo next_action: Restore kabu_native\scripts\run_paper_trade_opval.py
  pause
  exit /b 1
)

set "PYTHONPATH=%NATIVE%\src;%REPO%"
set "PYTHONIOENCODING=utf-8"
if not defined MARKET_INGRESS_V2 set "MARKET_INGRESS_V2=1"

python "%PY%" %*
set "RC=%ERRORLEVEL%"
exit /b %RC%
