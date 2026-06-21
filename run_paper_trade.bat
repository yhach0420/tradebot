@echo off
setlocal

echo [PAPER TRADE] starting...
echo [PAPER TRADE] repo=C:\Users\yhach\Documents\tradebotfile\kabu_native

cd /d C:\Users\yhach\Documents\tradebotfile\kabu_native

set PYTHONPATH=src

echo [PAPER TRADE] PYTHONPATH=%PYTHONPATH%
echo [PAPER TRADE] command:
echo python scripts\run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe

python scripts\run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe

echo.
echo [PAPER TRADE] finished with exit code %ERRORLEVEL%
pause

endlocal
