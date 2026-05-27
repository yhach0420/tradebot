# Phase 166: fade breakdown shadow recommendation

**Verdict:** `fade_hybrid_still_better`

## Scenario summary (subset=all)

| Scenario | PF | avg PnL | fade_exit | fade_deferred | breakdown_exit | max_loss | stop_hit |
|----------|----|---------|----------:|--------------:|--------------:|---------:|--------:|
| A_combined_v1 | 0.8905 | -0.0067 | 439 | 0 | 0 | -7.6923 | 3 |
| B_fade_hybrid_shadow | 0.8873 | -0.0104 | 0 | 439 | 94 | -7.6923 | 4 |
| C_fade_breakdown_shadow | 0.824 | -0.0288 | 0 | 439 | 124 | -7.6923 | 64 |
| D_fade_disable_shadow | 0.7884 | -0.0432 | 0 | 0 | 0 | -7.6923 | 117 |

## Live shadow command

```powershell
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow fade-breakdown
```

## Notes

- fade_breakdown PF 0.824 not above baseline 0.8905
- hybrid PF exceeds fade_breakdown

## Constraints

- Shadow only; order_enabled=false; paper_only=true; cap=3.
