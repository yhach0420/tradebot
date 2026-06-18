# Phase429A — No Progress Exit Capacity-Aware Audit

Generated: 2026-06-17T22:31:53+09:00
Verdict: **capacity_positive**
Policy: `linmfe_t900_i0p6_s0p05_c0p8_p0p3`

## Part A — Phase427/428 nature

- replay_type: **exit_only** (not capacity-aware)
- replaces exit time only: **True**
- frees CAP slots in replay: **False**
- re-evaluates rejected candidates: **False**
- +87,521 yen is exit-improvement only: **True**
- includes capacity reuse: **False**

## Part B — Comparison

| scenario | accepted | total PnL | PF | maxDD | delta vs baseline | no_progress exits |
|----------|----------|-----------|-----|-------|-------------------|-------------------|
| A_baseline_phase423 | 678 | 141767.98 | 1.1352 | 102282.41 | 0.0 | 0 |
| B_exit_only_no_progress | 678 | 229288.79 | 1.2458 | 80031.62 | 87520.81 | 90 |
| C_capacity_aware_no_progress | 681 | 239288.41 | 1.2552 | 80031.62 | 97520.43 | 90 |

## Capacity reuse

- exit-only vs baseline: **87520.81** yen (Phase428 ref 87520.81)
- capacity-aware vs baseline: **97520.43** yen
- capacity-aware vs exit-only: **9999.62** yen
- added trades: **3** (+9999.62 yen, PF 2.9998)
- symbols: 6976.T, 6981.T
- all 3 from baseline `insufficient_buying_power` rejects (CAP reject reduction: 0)
- capacity incremental PnL positive: **True**

## Integrity

- post_baseline_violations: **0**

## 必須回答

- 1_phase428_exit_only: True
- 2_delta_includes_capacity_reuse: False
- 3_capacity_aware_total_pnl: 239288.41
- 4_vs_baseline_delta: 97520.43
- 5_vs_exit_only_delta: 9999.62
- 6_new_accepted_count: 3
- 7_added_trades_pnl: 9999.62
- 8_cap_reject_reduction: 0
- 9_post_baseline_violations: 0
- 10_forward_shadow_ok: True

## Forward Shadow recommendation

**Proceed** — exit-only improvement confirmed on Phase423 snapshot; capacity-aware replay adds +9,999 yen with zero post_baseline violations.