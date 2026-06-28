# Phase559 — Paper Trade Readiness after Phase557/558

**Verdict:** `phase559_paper_trade_readiness_after_phase557_ok`

## Runtime under test

| Component | Status |
|-----------|--------|
| OR Overlay | enabled (CAP 4+1=5) |
| ClusterGuard V6+E4 | enabled |
| stop_low_mfe Guard G554_022 | enabled (0.009, missing→pass, PBv2 only) |
| ReEntry RSI Guard | enabled |
| Entry Quality Guard | enabled |

Production YAML: `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

## Mandatory checks (10/10)

| # | Check | Result |
|---|-------|--------|
| 1 | YAML stop_low_mfe guard config | PASS |
| 2 | `volume_acceleration_5m` live computable | PASS |
| 3 | missing→pass | PASS |
| 4 | OR exempt | PASS |
| 5 | PBv2 only | PASS |
| 6 | Summary/Discord SLM fields | PASS |
| 7 | Rollback (`stop_low_mfe_guard_enabled: false`) | PASS |
| 8 | Smoke test fast (~0.7s) | PASS |
| 9 | `run_paper_trade.bat` preflight path | PASS (preflight + smoke) |
| 10 | AM runner startup | PASS (startup-smoke + dry-run verified) |

## Command results (2026-06-27)

```text
phase557_ready (--skip-overlap)     ~0.7s  OK
production_smoke_test               ~0.7s  OK
live_pipeline_preflight             ~0.7s  OK
am_runner_startup_smoke             ~0.8s  OK
am_runner_dry_run (optional)        ~336s OK (exit 0, intraday_refresh_shadow_ready)
```

## Daily startup (same as `run_paper_trade.bat`)

```powershell
cd C:\Users\yhach\Documents\tradebotfile
python kabu_native/scripts/run_phase557_stop_low_mfe_guard_ready.py --skip-unit-tests --skip-overlap
python kabu_native/scripts/run_production_startup_smoke_test.py --exit-policy-shadow trailing-mfe
python kabu_native/scripts/check_live_pipeline_preflight.py
.\run_paper_trade.bat
```

Or single readiness script:

```powershell
python kabu_native/scripts/run_phase559_paper_trade_readiness.py
```

## Session monitoring

Watch in session summary / Discord:

- `stop_low_mfe_guard_reject_count`
- `stop_low_mfe_guard_missing_count`
- `stop_low_mfe_guard_blocked_winner`
- `stop_low_mfe_guard_blocked_big_winner`
- `stop_low_mfe_guard_net_shadow`
- `cluster_guard_reject_count`
- `cluster_guard_exception_count`
- `or_entry_count`

## Rollback

```yaml
stop_low_mfe_guard_enabled: false
```

## Outputs

- `results/reports/phase559_report.json`

## Notes

- Phase557 `--skip-overlap` skips ~5min overlap re-analysis; use full run only when auditing guard overlap.
- `run_paper_trade.bat` runs preflight → smoke → AM/PM runner (no overlap analysis).
