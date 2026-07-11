@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM タスク スケジューラ用ランチャー。診断は watchdog_launcher_YYYYMMDD.log へ。
set "CD_AT_LAUNCH=%CD%"

pushd "%~dp0.."
set "ROOT=%CD%"
popd

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "D=%%i"
set "RUNTIME=!ROOT!\logs\runtime"
if not exist "!RUNTIME!" mkdir "!RUNTIME!"
set "LAUNCHER=!RUNTIME!\watchdog_launcher_!D!.log"

>>"!LAUNCHER!" echo.
>>"!LAUNCHER!" echo [%date% %time%] === tradebot_watchdog launcher ===
>>"!LAUNCHER!" echo [%date% %time%] CD_AT_LAUNCH=!CD_AT_LAUNCH!
>>"!LAUNCHER!" echo [%date% %time%] ROOT=!ROOT!

pushd "!ROOT!"
>>"!LAUNCHER!" echo [%date% %time%] CD_AFTER_pushd_ROOT=!CD!
>>"!LAUNCHER!" echo [%date% %time%] whoami:
whoami >>"!LAUNCHER!" 2>&1
>>"!LAUNCHER!" echo [%date% %time%] where python:
where python >>"!LAUNCHER!" 2>&1
set "PY_FIRST="
for /f "delims=" %%P in ('where python 2^>nul') do (
  set "PY_FIRST=%%P"
  goto :pydone
)
:pydone
if defined PY_FIRST (
  >>"!LAUNCHER!" echo [%date% %time%] python --version ^(!PY_FIRST!^):
  "!PY_FIRST!" --version >>"!LAUNCHER!" 2>&1
) else (
  >>"!LAUNCHER!" echo [%date% %time%] python --version: SKIPPED ^(where python empty^)
)
>>"!LAUNCHER!" echo [%date% %time%] PATH=!PATH!
popd

set "WATCHDOG_DUP_LOG=!LAUNCHER!"
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0check_watchdog_running.ps1" 1>nul 2>nul
set "DUP_EL=!errorlevel!"

>>"!LAUNCHER!" echo [%date% %time%] check_watchdog_running.ps1 exitcode=!DUP_EL!
if !DUP_EL! equ 0 (
  >>"!LAUNCHER!" echo [%date% %time%] duplicate watchdog detected, not starting inner.
  >>"!LAUNCHER!" echo [%date% %time%] launcher_exit=0
  endlocal
  exit /b 0
)

set "INNER=%~dp0run_watchdog_inner.bat"
>>"!LAUNCHER!" echo [%date% %time%] EXEC: start "tradebot_watchdog" /MIN "%ComSpec%" /c call "!INNER!"
start "tradebot_watchdog" /MIN "%ComSpec%" /c call "!INNER!"
set "START_EL=!errorlevel!"
>>"!LAUNCHER!" echo [%date% %time%] start_inner_immediate_errorlevel=!START_EL!
>>"!LAUNCHER!" echo [%date% %time%] launcher_exit=0

endlocal
exit /b 0
