@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM 実体: ルート固定 + where python の先頭 + watchdog.py 絶対パス（タスク スケジューラの cwd/PATH 差を吸収）
set "ROOT=%~dp0.."
pushd "!ROOT!"
set "ROOT=!CD!"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "D=%%i"
set "RUNTIME=!ROOT!\logs\runtime"
if not exist "!RUNTIME!" mkdir "!RUNTIME!"
set "LOG=!RUNTIME!\watchdog_!D!.log"

set "PYTHON_EXE="
for /f "delims=" %%P in ('where python 2^>nul') do (
  set "PYTHON_EXE=%%P"
  goto :have_py
)
:have_py
if not defined PYTHON_EXE (
  >>"!LOG!" echo [%date% %time%] FATAL run_watchdog_inner: where python returned nothing PATH=!PATH!
  popd
  endlocal
  exit /b 1
)

set "WATCHDOG_SCRIPT=!ROOT!\scripts\watchdog.py"
set PYTHONUNBUFFERED=1

>>"!LOG!" echo.
>>"!LOG!" echo === run_watchdog_inner %date% %time% ===
>>"!LOG!" echo ROOT=!ROOT!
>>"!LOG!" echo PYTHON_EXE=!PYTHON_EXE!
>>"!LOG!" echo CD=!CD!
>>"!LOG!" echo WATCHDOG_SCRIPT=!WATCHDOG_SCRIPT!
>>"!LOG!" echo CMD="!PYTHON_EXE!" "!WATCHDOG_SCRIPT!"
>>"!LOG!" echo ========================================

"!PYTHON_EXE!" "!WATCHDOG_SCRIPT!" >>"!LOG!" 2>&1
set "EL=!errorlevel!"
>>"!LOG!" echo [%date% %time%] watchdog.py exited errorlevel=!EL!

popd
exit /b !EL!
