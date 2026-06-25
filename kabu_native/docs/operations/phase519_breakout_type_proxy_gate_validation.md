# Phase519 — Breakout-Type Proxy Gate Validation

**Verdict:** `phase519_breakout_type_proxy_gate_validation_done`
**Period:** 20260529 – 20260622
**Spread median:** 63.78 | **Board median:** 0.488215 (board missing 0.9571)

## Top success-criteria candidates

- **G3_G4**: PnL=274809.7, PF=1.5537, maxDD=46100.0, gates=G3,G4
- **G4_G5**: PnL=233709.61, PF=1.5886, maxDD=63799.82, gates=G4,G5

## Summary (BASE vs BASE_OR vs best)

- **BASELINE**: PnL=214959.61 PF=1.3476 maxDD=118600.0 success=False
- **O_R003_OR_BASE**: PnL=547110.65 PF=1.8533 maxDD=67099.41 success=False
- **G3_G4**: PnL=274809.7 PF=1.5537 maxDD=46100.0 success=True

## Mandatory answers

- **1_pbv2_improving_proxy_gate_exists**: True
- **1_improving_candidates**: ['G1', 'G2', 'G3', 'G4', 'G5', 'G1_G2', 'G1_G3', 'G1_G4', 'G2_G4', 'G2_G5']
- **2_robust_vs_base_or**: ['G3_G4']
- **2_success_criteria_candidates**: ['G3_G4', 'G4_G5']
- **3_pnl_pf_dd_improvement_candidates**: ['G1', 'G2', 'G3', 'G4', 'G5', 'G1_G2', 'G1_G3', 'G1_G4', 'G2_G4', 'G2_G5', 'G3_G4', 'G3_G5', 'G4_G5', 'G1_G2_G4']
- **4_6976_dependency_reduced**: ['G3_G4', 'G4_G5']
- **5_top10_trade_dependency_reduced**: ['G2', 'G3', 'G5', 'G1_G2', 'G1_G3', 'G1_G5', 'G2_G3', 'G2_G4', 'G2_G5', 'G3_G4', 'G3_G5', 'G4_G5', 'G1_G2_G3', 'G1_G2_G4', 'G1_G2_G5']
- **6_late_breakout_reduced**: ['G1', 'G2', 'G3', 'G4', 'G5', 'G1_G2', 'G1_G3', 'G1_G4', 'G1_G5', 'G2_G3', 'G2_G4', 'G2_G5', 'G3_G4', 'G3_G5', 'G4_G5', 'G1_G2_G3', 'G1_G2_G4', 'G1_G2_G5']
- **7_high_chase_reduced**: ['G4', 'G3_G4', 'G4_G5']
- **8_best_single_gate**: {'scenario_id': 'G4', 'pnl': 497600.81, 'success': False}
- **9_best_2gate**: {'scenario_id': 'G3_G4', 'pnl': 274809.7, 'success': True}
- **10_best_3gate**: {'scenario_id': 'G1_G2_G4', 'pnl': 299400.46, 'success': False}
- **11_proxy_gate_raises_adoption_potential**: True
- **12_next_phase_candidates**: ['G3_G4', 'G4_G5']
- **12_best_success_candidate**: G3_G4
- **13_production_adopt_ok**: False
- **13_adopt_not_allowed**: True
