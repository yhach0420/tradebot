# Shadow Portfolio Cleanup

**Verdict:** `SHADOW_PORTFOLIO_CLEANUP_DONE`

## Always-on Forward / Monitor (Paper)

| Class | Shadow | Notes |
|-------|--------|-------|
| ACTIVE_FORWARD | `e1_x5_forward_shadow` | Paper default ON; Live forced OFF; independent CAP5 |
| TEMP_FORWARD | `flat_weak_range_shadow` | Paper ON; time-boxed ADOPT/REJECT |
| MAINLINE_MONITOR | `board_dynamic_trailing_shadow` | Discord 1-line; does not change EXIT mainline |

## LOGGER_ONLY (no Discord PnL Summary)

- `w43f_evaluation_reachability`
- `pullback_volume_forward`

## Cost-Aware

- v1 → **RETIRED** (`COST_AWARE_ENTRY_SHADOW=0`)
- v2 → **DISABLED_RESEARCH** (no active Forward Gate)

## Discord

Usual Shadow Summary shows at most 3: E1_X5 / Flat Weak + Range / Board Dynamic Monitor.

Startup once:

```
Shadow Portfolio:
ACTIVE_FORWARD: ...
TEMP_FORWARD: ...
MAINLINE_MONITOR: ...
LOGGER_ONLY: ...
RETIRED: count=N
```

## Artifacts

`results/research/shadow_portfolio_cleanup/<run_id>/` → `report.md` / `report.json` / `audit.xlsx`
