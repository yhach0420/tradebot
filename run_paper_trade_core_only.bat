@echo off

setlocal



set REPO=C:\Users\yhach\Documents\tradebotfile

set CORE_RUNTIME_MODE=CORE_ONLY



echo [PAPER TRADE CORE_ONLY] starting...

echo [PAPER TRADE CORE_ONLY] core_runtime_mode=CORE_ONLY

echo [PAPER TRADE CORE_ONLY] repo=%REPO%



cd /d %REPO%



set PYTHONPATH=kabu_native\src



echo [PAPER TRADE CORE_ONLY] preflight: live ENTRY pipeline



python kabu_native\scripts\check_live_pipeline_preflight.py



if errorlevel 1 (

    echo [PAPER TRADE CORE_ONLY] aborted: live pipeline preflight failed

    pause

    exit /b 1

)



echo [PAPER TRADE CORE_ONLY] command:

echo python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe --core-runtime-mode CORE_ONLY



python kabu_native\scripts\run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe --core-runtime-mode CORE_ONLY



echo.

echo [PAPER TRADE CORE_ONLY] finished with exit code %ERRORLEVEL%

pause



endlocal

