@echo off
setlocal

set REPO=C:\Users\yhach\Documents\tradebotfile
set PRE625_RUNTIME_STRUCTURE_MODE=true

echo [PAPER TRADE PRE625 STRUCTURE] starting...
echo [PAPER TRADE PRE625 STRUCTURE] pre625_runtime_structure_mode=true
echo [PAPER TRADE PRE625 STRUCTURE] repo=%REPO%

cd /d %REPO%

set PYTHONPATH=kabu_native\src

echo [PAPER TRADE PRE625 STRUCTURE] PYTHONPATH=%PYTHONPATH%
echo [PAPER TRADE PRE625 STRUCTURE] preflight: live ENTRY pipeline

python kabu_native\scripts\check_live_pipeline_preflight.py

if errorlevel 1 (
    echo [PAPER TRADE PRE625 STRUCTURE] aborted: live pipeline preflight failed
    pause
    exit /b 1
)

echo [PAPER TRADE PRE625 STRUCTURE] preflight: production startup smoke test

python kabu_native\scripts\run_production_startup_smoke_test.py --exit-policy-shadow trailing-mfe

if errorlevel 1 (
    echo [PAPER TRADE PRE625 STRUCTURE] aborted: production startup smoke test failed
    pause
    exit /b 1
)

echo [PAPER TRADE PRE625 STRUCTURE] command:
echo python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe --pre625-runtime-structure-mode

python kabu_native\scripts\run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe --pre625-runtime-structure-mode

echo.
echo [PAPER TRADE PRE625 STRUCTURE] finished with exit code %ERRORLEVEL%
pause

endlocal
