# Phase554 — stop_low_mfe Entry Quality Feature Study

**Verdict:** `phase554_stop_low_mfe_entry_quality_feature_study_done`
**Live period:** 20260616-20260625
**Full period:** 20260529-20260625
**Live trades:** 148 | **Baseline PnL:** -30800.0

## Cohorts

- stop_low_mfe (stop_hit + MFE<0.6%): 120
- normal_winner (pnl>0, MFE>=0.8%): 19
- big_winner (MFE>=1.5%): 7
- mfe0: 65

## Loss attribution

- Live stop_low_mfe share of loss: -94.67%
- Full-period stop_low_mfe share of loss: -79.33%
- Live MFE0 share of loss: -26.58%

## Mandatory answers

- **10_shadow_candidates:** ['G554_021', 'G554_031']
- **11_runtime_candidates:** ['G554_022', 'G554_001', 'G554_002']
- **12_next_phase:** phase555_stop_low_mfe_guard_shadow_replay
- **1_full_period_stop_low_mfe_share_pct:** -79.33
- **1_live_window_stop_low_mfe_share_pct:** -94.67
- **1_stop_low_mfe_main_loss_driver_full_period:** True
- **2_separable_features_exist:** True
- **3_most_effective_feature:** spread
- **3_top_cohens_d:** -0.8815
- **4_volume_acceleration_effective:** True
- **4_volume_acceleration_rank:** 6
- **5_tick_speed_effective:** False
- **5_tick_speed_rank:** 15
- **6_high_update_persistence_effective:** False
- **6_high_update_persistence_rank:** 23
- **7_board_consumption_effective:** True
- **7_board_consumption_rank:** 5
- **8_618_6387_blocked:** True
- **8_618_6779_blocked:** True
- **8_618_6976_blocked:** True
- **8_618_blocked_symbols:** ['6779.T', '6976.T', '6387.T']
- **9_best_guard_big_winner_lost:** 2
- **9_best_guard_normal_winner_blocked:** 9.0
- **9_winner_over_cut_risk:** True
- **best_guard:** {'guard_id': 'G554_021', 'feature': 'volume_acceleration_5m', 'threshold': -0.019608, 'net_improvement_yen_100': 40200.0}
- **top5_features:** ['spread', 'update_count_before_entry', 'price_acceleration_decay', 'five_min_position', 'board_consumption_speed']

## Output files

- `results/reports/phase554_feature_separation.csv`
- `results/reports/phase554_feature_ranking.csv`
- `results/reports/phase554_guard_candidates.csv`
- `results/reports/phase554_20260618_counterfactual.csv`
- `results/reports/phase554_report.json`
