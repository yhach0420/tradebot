# Phase540 — NoProgress / MFE0 Entry Quality Root Cause Study

**Verdict:** `phase540_no_progress_mfe0_entry_quality_done`
**Generated:** 2026-06-25T22:02:35+09:00
**Days:** 20260625
**Trades:** 27

## Mandatory answers

1. **1_no_progress_exit_count:** 20
2. **2_mfe0_count_strict:** 6
3. **3_mfe0_primary_loss_driver:** True
4. **4_mfe0_common_traits:** {'mfe0_wider_spread': 'no', 'mfe0_worse_board_imbalance': 'yes', 'mfe0_weaker_volume': 'yes', 'mfe0_weaker_volume_ratio': 'yes', 'mfe0_worse_vwap_distance': 'no', 'mfe0_rsi_extreme': 'mixed', 'mfe0_bad_five_min_position': 'no', 'mfe0_not_high_update_recent': 'yes', 'mfe0_pullback_misread': 'partial', 'mfe0_counter_trend': 'partial'}
5. **5_top_separation_feature:** five_min_position
6. **6_mfe0_pullback_misread:** partial
7. **7_mfe0_counter_trend_bounce:** partial
8. **8_no_progress_exit_effective:** False
9. **9_preventable_at_entry:** True
10. **10_entry_guard_v2_candidates_exist:** True
11. **11_best_guard_candidate:** G12_mfe0_best_2feature
12. **12_production_adoption_candidate:** True
13. **13_next_phase:** Forward-shadow best guard on 5+ live days; validate MFE0 block rate vs lost winners.

## Hypothesis checks (MFE0 vs Winner)

- mfe0_wider_spread: no
- mfe0_worse_board_imbalance: yes
- mfe0_weaker_volume: yes
- mfe0_weaker_volume_ratio: yes
- mfe0_worse_vwap_distance: no
- mfe0_rsi_extreme: mixed
- mfe0_bad_five_min_position: no
- mfe0_not_high_update_recent: yes
- mfe0_pullback_misread: partial
- mfe0_counter_trend: partial

## Outputs

- `results/reports/phase540_no_progress_trades.csv`
- `results/reports/phase540_mfe0_trades.csv`
- `results/reports/phase540_mfe_bucket_summary.csv`
- `results/reports/phase540_entry_features.csv`
- `results/reports/phase540_feature_separation.csv`
- `results/reports/phase540_no_progress_analysis.csv`
- `results/reports/phase540_guard_v2_shadow.csv`
- `results/reports/phase540_report.json`

## Constraints

Research only. No Runtime / EXIT changes. No unlimited combinatorial search.
