# Phase542 — Guard v2 Threshold Tuning

**Verdict:** `phase542_guard_v2_threshold_tuning_done`
**Period:** 20260616 – 20260625 (all sessions)
**Strategies:** 33
**Trades:** 1309

## Top 3 by composite score

- #1 `ADX30_FIVE33` score=0.830282 PnL=73200.0 retention=0.1444
- #2 `ADX30_FIVE50` score=0.758822 PnL=61400.0 retention=0.1833
- #3 `ADX35_FIVE33` score=0.733759 PnL=48700.0 retention=0.1727

## Mandatory answers

- **1_g13_too_strong:** True
- **2_better_balance_examples:** []
- **2_better_balance_than_g13_exists:** False
- **3_best_adx_only:** ADX30
- **4_best_adx_five_min:** ADX30_FIVE33
- **5_best_adx_ma:** ADX30_MA013
- **6_group_d_representatives_valid:** True
- **7_retention_and_mfe0_balance_candidates:** ['ADX35_MA013', 'ADX35_MA025', 'ADX30', 'ADX40_FIVE66', 'ADX35']
- **8_lost_big_winner_better_than_g13:** ['ADX30', 'ADX35', 'ADX40', 'ADX45', 'ADX30_FIVE50', 'ADX30_FIVE66', 'ADX35_FIVE33', 'ADX35_FIVE50']
- **9_best_composite_score_guard:** ADX30_FIVE33
- **10_most_explainable_guard:** ADX35
- **11_shadow_forward_candidates:** ['ADX40', 'ADX30_FIVE33', 'ADX35_FIVE50', 'ADX40_FIVE50']
- **12_production_adoption_candidate:** False
- **13_next_phase:** Phase543: forward-shadow top 2–3 threshold guards on new live days.

## Outputs

- `results/reports/phase542_guard_v2_threshold_summary.csv`
- `results/reports/phase542_guard_v2_threshold_daily.csv`
- `results/reports/phase542_guard_v2_threshold_dependency.csv`
- `results/reports/phase542_guard_v2_threshold_ranking.csv`
- `results/reports/phase542_report.json`

Research only. No Runtime / EXIT adoption.
