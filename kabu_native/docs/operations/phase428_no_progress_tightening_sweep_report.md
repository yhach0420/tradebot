# Phase428 — Time-Tightening No Progress Exit Parameter Sweep

Generated: 2026-06-17T21:44:38+09:00
Verdict: **adopt_candidate_found**

Policies evaluated: 2262 on 678 trades

## Phase427 fixed reference

- fixed_900_mfe0.8_pnl0.2: delta 81920.69 yen (ref 81920.69)

## Best policy

- key: linmfe_t900_i0p6_s0p05_c0p8_p0p3
- type: linear_mfe
- spec: start=900s mfe=0.6+*0.05/5m cap=0.8 pnl<0.3
- delta PnL: 87520.81
- delta PF: 0.1106
- delta DD: -22250.79
- adopt_candidate: False

## Rank A top 5

- linmfe_t900_i0p6_s0p05_c0p8_p0p3: delta 87520.81
- linmfe_t900_i0p6_s0p05_c1p0_p0p3: delta 87520.81
- linmfe_t900_i0p6_s0p05_c1p2_p0p3: delta 87520.81
- linmfe_t900_i0p6_s0p05_c1p5_p0p3: delta 87520.81
- linmfe_t900_i0p6_s0p1_c0p8_p0p3: delta 87520.81

## 必須回答
- 1_best_policy: linmfe_t900_i0p6_s0p05_c0p8_p0p3
- 2_vs_phase427_fixed: {'fixed_delta': 81920.69, 'best_delta': 87520.81, 'delta_improvement_vs_fixed': 5600.12, 'beats_fixed': True}
- 3_pnl_improvement: 87520.81
- 4_pf_improvement: 0.1106
- 5_dd_improvement: -22250.79
- 6_large_damage_rescue: {'large_damage': 3, 'large_rescue': 4}
- 7_saved_lost: {'saved_loss_yen': 1016723.5, 'lost_upside_yen': 1154552.72}
- 8_pm_rescue: {'linmfe_t900_i0p6_s0p05_c0p8_p0p3': {'6976.T': {'rescue_possible': False, 'note': 'eval_failed'}, '5016.T': {'rescue_possible': False, 'note': 'eval_failed'}, '3915.T': {'rescue_possible': False, 'note': 'eval_failed'}, '5367.T': {'rescue_possible': False, 'note': 'eval_failed'}, '186A.T': {'rescue_possible': False, 'note': 'eval_failed'}}, 'fixed_900_mfe0.8_pnl0.2': {'6976.T': {'rescue_possible': False, 'note': 'eval_failed'}, '5016.T': {'rescue_possible': False, 'note': 'eval_failed'}, '3915.T': {'rescue_possible': False, 'note': 'eval_failed'}, '5367.T': {'rescue_possible': False, 'note': 'eval_failed'}, '186A.T': {'rescue_possible': False, 'note': 'eval_failed'}}}
- 9_adopt_candidate: False
- 9_strict_adopt_candidate: False
- 9_pnl_gated_adopt: True
- 10_forward_shadow_conditions: Forward shadow when: post_baseline=0 on forward days; saved_loss>lost_upside (currently fails for all policies); affected<=50%; large_rescue>=large_damage