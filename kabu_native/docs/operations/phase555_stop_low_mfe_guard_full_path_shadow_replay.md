# Phase555 — stop_low_mfe Guard Full-Path Shadow Replay

**Verdict:** `phase555_stop_low_mfe_guard_full_path_shadow_replay_done`
**Period:** 20260616-20260625
**Trades:** 148 | **Baseline PnL:** -30800.0

## Guard variants

| ID | Label | PnL | PF | net_improve | retention | lost_big | score | class |
|----|-------|-----|-----|-------------|-----------|----------|-------|-------|
| V0 | Baseline current runtime | -30800.0 | 0.7654 | 0.0 | 1.0 | 0 | 4 | baseline |
| V1 | G554_021 hard reject | 9400.0 | 1.1577 | 40200.0 | 0.527 | 7 | 6 | B_shadow_candidate |
| V2 | G554_031 hard reject | -13900.0 | 0.8178 | 16900.0 | 0.7365 | 6 | 6 | B_shadow_candidate |
| V3 | G554_022 hard reject | -3400.0 | 0.9624 | 27400.0 | 0.6824 | 1 | 8 | A_runtime_candidate |
| V4 | G554_021 + re-entry rescue | -16300.0 | 0.8107 | 14500.0 | 0.5405 | 6 | 6 | B_shadow_candidate |
| V5 | G554_021 + liquidity_burst rescue | 9400.0 | 1.1577 | 40200.0 | 0.527 | 7 | 6 | B_shadow_candidate |
| V6 | G554_021 + high_update rescue | -24900.0 | 0.7412 | 5900.0 | 0.6959 | 7 | 6 | B_shadow_candidate |

## Mandatory answers

- **10_shadow_candidates:** ['V1', 'V2', 'V4', 'V5', 'V6']
- **11_runtime_candidates:** ['V3']
- **12_next_phase:** phase556_stop_low_mfe_guard_production_readiness
- **1_G554_021_effective:** True
- **2_G554_031_effective:** True
- **3_G554_022_effective:** True
- **4_618_top3_blocked:** {'6779': False, '6976_am': True, '6387': True}
- **5_6976_pm_winner_kept_reentry:** True
- **6_reentry_rescue_effective:** True
- **7_liquidity_burst_rescue_effective:** True
- **8_high_update_rescue_effective:** True
- **9_winner_over_cut_risk:** True
- **V1_summary:** {'pnl_yen_100': 9400.0, 'profit_factor': 1.1577, 'net_improvement_yen_100': 40200.0, 'retention': 0.527, 'lost_big_winner': 7}
- **V4_day618_net:** 45300.0
- **baseline_pnl_yen_100:** -30800.0
- **best_variant:** V3

## Output files

- `results/reports/phase555_guard_replay_summary.csv`
- `results/reports/phase555_guard_replay_detail.csv`
- `results/reports/phase555_20260618_detail.csv`
- `results/reports/phase555_dependency_audit.csv`
- `results/reports/phase555_report.json`
