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
REM Cost-Aware v1/v2 RETIRED — do not auto-enable
if not defined COST_AWARE_ENTRY_SHADOW set COST_AWARE_ENTRY_SHADOW=0
if not defined COST_AWARE_ENTRY_V2_SHADOW set COST_AWARE_ENTRY_V2_SHADOW=0
if not defined PULLBACK_VOLUME_FORWARD set PULLBACK_VOLUME_FORWARD=1
REM E1_X5 Forward Shadow: Paper default ON in code (leave unset; set E1_X5_FORWARD_SHADOW=0 to disable)
REM V1R EXIT V2 live Primary dual-lane (Arch E + FIXED600 Control)
if not defined V1R_EXIT_V2_LIVE_PRIMARY set V1R_EXIT_V2_LIVE_PRIMARY=1
REM PBv2 Discord: SHADOW_ONLY → research (NOT trade-notify). Occupancy untouched.
if not defined V1R_PBV2_NOTIFICATION_ROUTING_ONLY set V1R_PBV2_NOTIFICATION_ROUTING_ONLY=1

echo [PAPER TRADE] PYTHONPATH=%PYTHONPATH%
echo [PAPER TRADE] COST_AWARE_ENTRY_SHADOW=%COST_AWARE_ENTRY_SHADOW% (RETIRED)
echo [PAPER TRADE] COST_AWARE_ENTRY_V2_SHADOW=%COST_AWARE_ENTRY_V2_SHADOW% (RETIRED)
echo [PAPER TRADE] PULLBACK_VOLUME_FORWARD=%PULLBACK_VOLUME_FORWARD% (LOGGER_ONLY)
if defined E1_X5_FORWARD_SHADOW (
  echo [PAPER TRADE] E1_X5_FORWARD_SHADOW=%E1_X5_FORWARD_SHADOW%
) else (
  echo [PAPER TRADE] E1_X5_FORWARD_SHADOW=^(unset — Paper default ON^)
)

echo [PAPER TRADE] MARKET_INGRESS_V2=%MARKET_INGRESS_V2%
echo [PAPER TRADE] V1R_EXIT_V2_LIVE_PRIMARY=%V1R_EXIT_V2_LIVE_PRIMARY%
echo [PAPER TRADE] V1R_PBV2_NOTIFICATION_ROUTING_ONLY=%V1R_PBV2_NOTIFICATION_ROUTING_ONLY% (PBv2 ENTRY/EXIT→research; no occupancy impact)
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

REM === V1R Paper Primary preflight: role assertion ONLY (no long-running launcher) ===
echo [PAPER TRADE] preflight: V1R Paper Primary role assertion (fail-closed, assert-only)
python -m small_paper.v1r_paper_primary_launcher --assert-only
if errorlevel 1 (
    echo [PAPER TRADE] aborted: V1R_PRIMARY_ROLE_ASSERTION_FAILED
    echo [PAPER TRADE] NO PAPER PRIMARY — classic PBv2 Primary fallback FORBIDDEN
    pause
    exit /b 1
)

echo [PAPER TRADE] V1R is PAPER_PRIMARY. Classic trailing-MFE Primary path DISABLED.
echo [PAPER TRADE] PBv2 role=SHADOW_ONLY (not started as Primary).
echo [PAPER TRADE] command:
echo python -m small_paper.v1r_paper_primary_launcher --mode live

set V1R_PRIMARY_BOUND=1
python -m small_paper.v1r_paper_primary_launcher --mode live
set PAPER_EXIT=%ERRORLEVEL%

echo.
echo [PAPER TRADE] finished with exit code %PAPER_EXIT%
pause

endlocal
exit /b %PAPER_EXIT%
