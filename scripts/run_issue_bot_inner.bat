@echo off
REM 起動本体。確認手順は README.md「Issue Bot（bat 経由）の動作確認」参照。
setlocal EnableExtensions
set "ROOT=%~dp0.."
pushd "%ROOT%"
set "ROOT=%CD%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "D=%%i"
set "RUNTIME=%ROOT%\logs\runtime"
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
set "LOG=%RUNTIME%\issue_bot_%D%.log"

>>"%LOG%" echo.
>>"%LOG%" echo === run_issue_bot_inner %date% %time% ===
>>"%LOG%" echo ROOT=%ROOT%
>>"%LOG%" echo LOG=%LOG%
>>"%LOG%" echo where python:
where python >>"%LOG%" 2>&1
>>"%LOG%" echo python --version:
python --version >>"%LOG%" 2>&1
>>"%LOG%" echo CD=%CD%
>>"%LOG%" echo CMD=python .\discord_issue_bot\discord_issue_bot.py
>>"%LOG%" echo ========================================

python .\discord_issue_bot\discord_issue_bot.py >>"%LOG%" 2>&1

popd
endlocal
