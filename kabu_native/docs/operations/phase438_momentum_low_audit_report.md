# Phase438 — Momentum Low Audit

Generated: 2026-06-18T22:26:16+09:00
Period: 20260529..20260618
**Verdict:** `high_drift_preferred`

## Mandatory answers

1. 6976型件数: **180**
2. Momentum low のうち15m下落割合: **0.3954**
3. 15m下落群PF: **0.9883**
4. 15m上昇群PF: **1.0763**
5. 6976は典型か: **True** (drift_share_all=0.2965, drift_share_6976=0.3784)
6. Momentum low の弱点: **momentum_low admits negative 15m drift far from day high (downtrend bounce) in dynamic40**
7. 最も有効な改善候補: **mom_low_and_high_drift_excluded**
8. High Drift で代替すべきか: **True**
9. Momentum定義修正すべきか: **False**
10. Runtime Shadow候補: **True**

## Part C — drifting_winner_misclassification

- count: 180
- PnL: -6,486 yen
- PF: 0.9827
- stop_rate: 0.2556

## Part D — Momentum low quality by 15m return

| bucket | count | PnL | PF | stop_rate |
|--------|-------|-----|----|----------|
| A_up | 251 | 25,308 | 1.0763 | 0.243 |
| B_flat | 26 | -13,502 | 0.5807 | 0.2308 |
| C_down | 240 | -6,085 | 0.9883 | 0.2458 |
| unknown | 90 | 72,099 | 1.555 | 0.3 |

## Part E — Shadow comparison (cohort = Momentum:low only)

| variant | trades | PnL | PF | stop_rate | maxDD |
|---------|--------|-----|----|----------|-------|
| baseline_momentum_low | 607 | 77,820 | 1.0769 | 0.2521 | 158,700 |
| mom_low_and_15m_gt_0 | 252 | 25,908 | 1.0781 | 0.2421 | 67,070 |
| mom_low_and_high_drift_excluded | 568 | 168,819 | 1.2023 | 0.2447 | 67,297 |
| mom_low_and_not_far_from_day_high_3pct | 224 | -29,905 | 0.9276 | 0.2679 | 142,204 |
| mom_low_and_within_15m_of_day_high_update | 150 | 83,799 | 1.37 | 0.26 | 51,200 |

Runtime/YAML/Entry/Exit/Order/Discord changes **forbidden** (audit only).
