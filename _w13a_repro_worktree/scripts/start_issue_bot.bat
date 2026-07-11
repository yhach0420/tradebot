@echo off
REM 確認手順: README.md「Issue Bot（bat 経由）の動作確認」参照。
setlocal EnableExtensions
pushd "%~dp0.."
set "ROOT=%CD%"
popd

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "D=%%i"
set "RUNTIME=%ROOT%\logs\runtime"
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
set "LOG=%RUNTIME%\issue_bot_%D%.log"

set "ISSUE_BOT_ROOT=%ROOT%"
set "ISSUE_BOT_DUP_LOG=%LOG%"

REM 重複チェックは scripts\check_issue_bot_running.ps1（インライン PowerShell は cmd の ^ 解釈で壊れやすい）
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0check_issue_bot_running.ps1" 1>nul 2>nul

if %errorlevel% equ 0 (
  exit /b 0
)

>>"%LOG%" echo.
>>"%LOG%" echo [%date% %time%] start_issue_bot: launching inner bat
>>"%LOG%" echo [%date% %time%] ROOT=%ROOT%
>>"%LOG%" echo [%date% %time%] LOG=%LOG%

set "INNER=%~dp0run_issue_bot_inner.bat"
start "discord_issue_bot" /MIN "%ComSpec%" /c "%INNER%"

exit /b 0
