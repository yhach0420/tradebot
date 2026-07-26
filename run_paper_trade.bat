@echo off



setlocal







set REPO=C:\Users\yhach\Documents\tradebotfile







echo [PAPER TRADE] starting...



echo [PAPER TRADE] repo=%REPO%







cd /d %REPO%







set PYTHONPATH=kabu_native\src

REM Shadow Portfolio Cleanup: Paper runtime + LOGGER_ONLY / ACTIVE_FORWARD defaults
if not defined KABU_PAPER_RUNTIME set KABU_PAPER_RUNTIME=1
REM Market Ingress V2: Independent WS owner + Raw-first Capture (cutover after preflight PASS)
if not defined MARKET_INGRESS_V2 set MARKET_INGRESS_V2=1
REM Cost-Aware v1/v2 RETIRED ??do not auto-enable
if not defined COST_AWARE_ENTRY_SHADOW set COST_AWARE_ENTRY_SHADOW=0
if not defined COST_AWARE_ENTRY_V2_SHADOW set COST_AWARE_ENTRY_V2_SHADOW=0
if not defined PULLBACK_VOLUME_FORWARD set PULLBACK_VOLUME_FORWARD=1
REM E1_X5 Forward Shadow: Paper default ON in code (leave unset; set E1_X5_FORWARD_SHADOW=0 to disable)

echo [PAPER TRADE] PYTHONPATH=%PYTHONPATH%
echo [PAPER TRADE] COST_AWARE_ENTRY_SHADOW=%COST_AWARE_ENTRY_SHADOW% (RETIRED)
echo [PAPER TRADE] COST_AWARE_ENTRY_V2_SHADOW=%COST_AWARE_ENTRY_V2_SHADOW% (RETIRED)
echo [PAPER TRADE] PULLBACK_VOLUME_FORWARD=%PULLBACK_VOLUME_FORWARD% (LOGGER_ONLY)
if defined E1_X5_FORWARD_SHADOW (
  echo [PAPER TRADE] E1_X5_FORWARD_SHADOW=%E1_X5_FORWARD_SHADOW%
) else (
  echo [PAPER TRADE] E1_X5_FORWARD_SHADOW=^(unset ??Paper default ON^)
)







echo [PAPER TRADE] MARKET_INGRESS_V2=%MARKET_INGRESS_V2%

echo [PAPER TRADE] preflight: Market Ingress V2 cutover

python kabu_native\scripts\run_market_ingress_v2_preflight.py

if errorlevel 1 (

    echo [PAPER TRADE] aborted: MARKET_INGRESS_V2_CUTOVER_BLOCKED

    pause

    exit /b 1

)


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



