# Phase558 — Current Runtime Replay after Phase557

**Verdict:** `phase558_current_runtime_after_phase557_done`
**Generated:** 2026-06-27T19:19:48+09:00
**Live period:** 20260616-20260625
**Full period:** 20260529-20260625

## Variants

- **C_legacy:** PBv2 only, no OR, no guards.
- **B_phase551:** Phase551 runtime (OR + ReEntry RSI + Entry Quality + ClusterGuard V6+E4).
- **D_phase558:** Phase558 latest (+ stop_low_mfe G554_022, threshold 0.009, missing→pass, PBv2 only).

## Mandatory answers

1. **Latest full-period PnL:** 598839.61 yen
2. **Latest PF:** 2.1397
3. **Latest maxDD:** 66120.0 yen
4. **Improved vs Phase551:** True (delta 11000.0)
5. **SLM guard contributed:** True (live delta 11000.0)
6. **Big winner over-cut:** True (blocked big=4)
7. **OR unaffected:** True
8. **Equity @1M:** fixed_100 final=1,013,530 ret=1.35% maxDD=47,400; max_capital_20pct final=1,002,380 ret=0.24% skips=104
9. **Equity @3M:** fixed_100 final=3,013,530 ret=0.45% maxDD=47,400; max_capital_20pct final=3,009,430 ret=0.31% skips=46
10. **Equity @5M:** fixed_100 final=5,013,530 ret=0.27% maxDD=47,400; max_capital_20pct final=5,037,030 ret=0.74% skips=37
11. **Runtime fixed OK:** False
12. **Next priority:** review_slm_threshold_if_big_winner_cut_rises

## Comparison (full period)

| Variant | Trades | PnL | PF | maxDD | SLM reject | blocked big |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C_legacy | 1557 | 22689.61 | 1.0113 | 339919.82 | 0 | 0 |
| B_phase551 | 483 | 587839.61 | 2.0279 | 69120.0 | 0 | 0 |
| D_phase558 | 432 | 598839.61 | 2.1397 | 66120.0 | 58 | 4 |

## Guard contribution

- **OR_plus_guards_vs_legacy_live:** delta=230050.0 blocked=0 — Phase551 vs Legacy live window
- **ClusterGuard_net_live:** delta=230050.0 blocked=0 — ClusterGuard+E4 on accepted set (Phase551)
- **stop_low_mfe_guard_live:** delta=11000.0 blocked=58 — Phase558 vs Phase551 live window (G554_022)
- **stop_low_mfe_guard_full_period:** delta=11000.0 blocked=58 — Combined CAP extension + live
- **OR_unchanged_by_slm:** delta=0.0 blocked=0 — OR PnL delta Phase558 vs Phase551 (expect 0)
- **slm_blocked_net_shadow:** delta=53700.0 blocked=58 — PnL of trades blocked by SLM guard only

## Outputs

- `results/reports/phase558_runtime_comparison_summary.csv`
- `results/reports/phase558_runtime_daily.csv`
- `results/reports/phase558_equity_simulation.csv`
- `results/reports/phase558_guard_contribution.csv`
- `results/reports/phase558_report.json`
