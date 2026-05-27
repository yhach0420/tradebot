# Phase 162: fade hybrid shadow recommendation

**Verdict:** `replay_mismatch`

## Scenario summary (subset=all)

| Scenario | PF | avg PnL | fade exits | fade_watch_entered | hybrid exits | max_loss | session_close |
|----------|----|---------|-----------:|-------------------:|------------:|---------:|-------------:|
| A_combined_v1 | 0.8905 | -0.0067 | 439 | 0 | 0 | -7.6923 | 2 |
| B_fade_hybrid_shadow | 0.8873 | -0.0104 | 0 | 439 | 340 | -7.6923 | 0 |
| C_breakdown_confirmed_shadow | 0.7884 | -0.0432 | 0 | 0 | 0 | -7.6923 | 0 |
| D_fade_disable_shadow | 0.7884 | -0.0432 | 0 | 0 | 0 | -7.6923 | 0 |

## Daily runner command

```powershell
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow fade-hybrid
```

## Notes

- hybrid PF 0.8873 not materially above baseline 0.8905

## Constraints

- Shadow only; order_enabled=false; paper_only=true; cap=3.
