# Market Capture Operations

## User command (unchanged)

```bat
cd C:\Users\yhach\Documents\tradebotfile
.\run_paper_trade_checked.bat
```

## Checked runner order (W9 / W15B)

1. JST trading date
2. Disk guard
3. Kabu read-only readiness
4. Universe prebuild / validate (same-day SoT; auto `build_am_universe` if missing)
5. Universe resolve (existing SoT, ≤50)
6. Registration coordination (lock)
7. Capture Sidecar start
8. Wait `CAPTURE_ONLINE`
9. Cache prebuild
10. Pipeline preflight
11. Smoke
12. Recovery / design / safety flags
13. Existing `run_paper_trade.bat` (once)
14. W4S
15. Capture continues to 15:35 JST
16. Capture finalize
17. Capture summary/seal verify

Universe prebuild never copies a previous trading day's CSV. Fail-closed on generation/validation failure.

## Paper blocked — Capture continues

If steps 8–11 fail after Capture is online:

```
[PAPER BLOCKED - CAPTURE CONTINUES]
```

Sidecar is **not** stopped.

## Capture start failure

Default: block Paper with `CAPTURE_REQUIRED_NOT_READY`.

Override (explicit only):

```
--allow-paper-without-capture
```

Shows large warning; real orders stay disabled; logs capture unavailable.

## Manual sidecar

```powershell
.\kabu_native\scripts\run_market_capture_sidecar.ps1
```

## Readiness CLI

```
python -m small_paper.check_market_capture_readiness
```

## Operator stop

Write `operator_stop.flag` under the day capture directory.

## Restart

Max **1** auto-restart via `market_capture_supervisor` (live / PS1).
Restart opens a **new** part (no append). History in `restart_history.jsonl`.

## Intraday refresh notify

After Paper completes 10:00 / 14:30 register, `notify_registration_refresh`
updates `runtime/market_registration_manifest.json` (Sidecar follower).
Does not change universe selection logic.

## Discord

At most 3 messages: STARTED / DEGRADED / FINISHED. Missing webhook → continue.
