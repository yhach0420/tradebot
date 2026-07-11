# ADR-687W8: One-Command Paper Trade Orchestrator

- **Status:** Accepted (launcher only — strategy / existing bat unchanged)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w8_paper_trade_checked_runner/`

## Context

Operators had to remember cache prebuild, Kabu readonly, preflight, smoke,
recovery, and post W4S evaluation order. Mistakes caused Paper starts without
readiness or skipped post evaluation.

## Decision

Provide a single entrypoint:

```bat
cd C:\Users\yhach\Documents\tradebotfile
.\run_paper_trade_checked.bat
```

Flow:

1. JST trading date (runtime, never fixed)
2. Disk guard (block CRITICAL+)
3. vol_liq cache prebuild
4. Kabu readonly readiness
5. Live pipeline preflight
6. Production startup smoke
7. Recovery readiness
8. Design consistency
9. Production enablement readiness (**info only**; NOT_AUTHORIZED expected)
10. Safety flags (live/order false, HARD_FAIL, no write adapter, no authorization)
11. Call existing `run_paper_trade.bat` **once** (no logic change, no auto-retry)
12. Post: W4S forward soak evaluator once + seal/snapshot summary

PYTHONPATH is set by the launcher. Secrets are redacted from logs.

## Consequences

- `run_paper_trade_checked.bat` + `kabu_native/scripts/run_paper_trade_checked.ps1`
- Module: `src/small_paper/paper_trade_checked_runner.py`
- Existing `run_paper_trade.bat` remains the Paper AM/PM runner SoT
- PRODUCTION ORDER ENABLEMENT remains NOT AUTHORIZED / NOT IMPLEMENTED
