@echo off



setlocal







set REPO=C:\Users\yhach\Documents\tradebotfile







echo [PAPER TRADE] starting...



echo [PAPER TRADE] repo=%REPO%







cd /d %REPO%







set PYTHONPATH=kabu_native\src







echo [PAPER TRADE] PYTHONPATH=%PYTHONPATH%







echo [PAPER TRADE] preflight: live ENTRY pipeline



python kabu_native\scripts\check_live_pipeline_preflight.py



if errorlevel 1 (



    echo [PAPER TRADE] aborted: live pipeline preflight failed



    pause



    exit /b 1



)







echo [PAPER TRADE] preflight: production startup smoke test



python kabu_native\scripts\run_production_startup_smoke_test.py --exit-policy-shadow trailing-mfe



if errorlevel 1 (



    echo [PAPER TRADE] aborted: production startup smoke test failed



    pause



    exit /b 1



)







echo [PAPER TRADE] command:



echo python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe







python kabu_native\scripts\run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe







echo.



echo [PAPER TRADE] finished with exit code %ERRORLEVEL%



pause







endlocal


