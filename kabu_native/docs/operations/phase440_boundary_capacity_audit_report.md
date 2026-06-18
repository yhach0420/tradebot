# Phase440 — Boundary Exit Capacity-Aware Audit

Generated: 2026-06-18T23:18:25+09:00
Verdict: **boundary_reconsider**
Period: 20260529..20260618

## Comparison (A/B/C)

| scenario | accepted | PnL | PF | maxDD | boundary exits | freed slots | delta vs baseline |
|----------|----------|-----|-----|-------|----------------|-------------|-------------------|
| A_baseline | 810 | 47567.98 | 1.0367 | 158700.0 | 0 | 0 | 0.0 |
| B_boundary_exit_only | 810 | 242768.07 | 1.305 | 75690.62 | 326 | 0 | 195200.09 |
| C_boundary_capacity_aware | 818 | 134567.69 | 1.1272 | 172600.87 | 328 | 308 | 86999.71 |

## Capacity effect

- boundary_exit_count (C): **328**
- freed_slots: **308**
- newly accepted: **10** (7 symbols)
- additional PnL from added trades: **-4000.38** yen
- additional PF (added only): **0.726**

## Pairwise deltas

- baseline vs exit-only PnL: **195200.09**
- baseline vs capacity-aware PnL: **86999.71**
- exit-only vs capacity-aware (capacity contribution): **-108200.38**
- added trade symbols: 186A.T, 4062.T, 581A.T, 6966.T, 6976.T, 6981.T, 7220.T

## Integrity

- post_baseline_violations: **0**

## 必須回答

- 1_boundary_exit_count: 328
- 2_cap_freed_slots: 308
- 3_added_accept_count: 10
- 4_added_accept_pnl_yen: -4000.38
- 5_exit_only_delta_vs_baseline: 195200.09
- 6_capacity_aware_delta_vs_baseline: 86999.71
- 7_capacity_contribution_vs_exit_only: -108200.38
- 8_pf_change_capacity_vs_baseline: 0.0905
- 9_maxdd_change_capacity_vs_baseline: 13900.87
- 10_boundary_still_low_value: False
- boundary_low_value_on_exit_only: False

## 判定

**boundary_reconsider** — Mixed or integrity-limited signal — reconsider boundary policy.