# Phase575 — Vol/Liq Startup Cache Production Adoption

**Verdict:** `phase575_vol_liq_startup_cache_production_adopted`
**All pass:** True
**Generated:** 2026-06-28T04:26:24+09:00

## Scope

Phase574-validated Vol/Liq startup cache wired into production `build_vol_liq_threshold()`.
Startup acceleration only — no ENTRY/EXIT/Universe/trading logic changes.

## Mandatory answers

1. Production path cache wired: True
2. AM session effective: True
3. PM session effective: True
4. Safety preflight effective: True
5. make_exposure_gate effective: True
6. Score match rate: 100.0%
7. Threshold match rate: 100.0%
8. Universe match rate: 100.0%
9. Entry/exit/pnl match rate: 100.0%
10. Fallback OK: True
11. Rollback possible: True (`vol_liq_startup_cache_enabled: false`)
12. Startup seconds saved (est): 890.0s
13. run_paper_trade OK: True
14. Next phase: phase576_vol_liq_cache_live_monitor

## Config

```yaml
vol_liq_startup_cache_enabled: true
vol_liq_startup_cache_dir: kabu_native/results/cache/vol_liq_startup
vol_liq_startup_cache_fallback_on_error: true
vol_liq_startup_cache_write_after_fallback: true
```

## Production call sites

- `build_vol_liq_threshold()` → `build_vol_liq_threshold_with_startup_cache()`
- `make_exposure_gate()` (config.py)
- `safety.py` preflight
- `live_observer_readiness.py`

## Session summary fields

- `vol_liq_cache_status`
- `vol_liq_cache_hit`
- `vol_liq_cache_fallback`
- `vol_liq_cache_seconds_saved`
- `vol_liq_cache_path`

## Validation

- Sessions validated: 45/45 cache hits
- Equivalence vs Phase574 baseline snapshots: 100% score/threshold/summary/entry
- Fallback scenarios: missing, corrupt, wrong_run_key, checksum_invalid, disabled

## Outputs

- `results/reports/phase575_production_cache_adoption.csv`
- `results/reports/phase575_startup_smoke.csv`
- `results/reports/phase575_am_pm_cache_check.csv`
- `results/reports/phase575_fallback_check.csv`
- `results/reports/phase575_equivalence.csv`
- `results/reports/phase575_report.json`