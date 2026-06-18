# Phase423 — Canonical Runtime Rebaseline (Post-Phase421)

Generated: 2026-06-17T00:44:43+09:00
Verdict: **canonical_baseline_established**

## Canonical Runtime (正式Baseline)

- same_symbol_open_policy: `no_overlap_replace`
- max_concurrent_positions: **5**
- position_cap_mode: `True`
- stop: `fixed_stop_1p2`
- paper_only / order_enabled: `True` / `False`

## 必須回答

1. **最新Runtime正式Baseline**: Phase423 canonical (no_overlap_replace + CAP5 + fixed_stop_1p2, 1.5M lev2)
2. **5/29〜6/16結果**: accepted=678, rejected=3, PnL=141767.98 yen (1417.68 yen/100), PF=1.1352, maxDD=102282.41 yen
3. **Phase399との差**: ΔPnL=4900.52 yen, ΔPF=0.0187, ΔmaxDD=-3019.52 yen, Δaccepted=-629
4. **Phase413との差**: ΔPnL=11000.38 yen, ΔPF=0.0118, ΔmaxDD=0.0 yen (Phase413 is structural shadow (681); Phase423 is CAP5 capital-accepted set)
5. **PF**: 1.1352
6. **PnL**: 141767.98 yen
7. **maxDD**: 102282.41 yen
8. **trade_count**: input=681, accepted=678
9. **hold時間**: avg=698.63s, median=313.0s
10. **Boundary対象率**: eligible_rate=0.550147 (373/678 accepted), hit=0
11. **今後の比較基準として採用するか**: **採用** (20260617以降のForwardは本Baselineと比較)

## Phase399 → Phase413 → Phase423

| Phase | trade_count | accepted | rejected | PF | PnL (yen) | maxDD | avg_hold | median_hold |
|-------|-------------|----------|----------|-----|-----------|-------|----------|-------------|
| 399 | 1529 | 1307 | 222 | 1.1165 | 136867.46 | 105301.93 | 353.43 | 78.5 |
| 413 | 681 | 681 | 0 | 1.1234 | 130767.6 | 102282.41 | 697.84 | 313.0 |
| 423 | 681 | 678 | 3 | 1.1352 | 141767.98 | 102282.41 | 698.63 | 313.0 |

## Overlap / Reject breakdown (Phase423)

- overlap_replaced_review (structural B): 151
- collapse reduction (A→B): 848
- buying_power_reject: 3
- reject_reason_breakdown: `{'insufficient_buying_power': 3}`

## Outputs

- `results/reports/phase423_runtime_canonical_rebaseline_summary.json`
- `results/reports/phase423_runtime_canonical_rebaseline_daily.csv`
- `results/reports/phase423_runtime_canonical_rebaseline_trades.csv`
- `results/reports/phase423_runtime_vs_phase399_phase413.csv`
