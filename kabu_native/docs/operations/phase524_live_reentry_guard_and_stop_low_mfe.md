# Phase524 — Live Re-Entry Guard + Stop Low MFE

**Verdict:** `phase524_live_reentry_guard_and_stop_low_mfe_done`
**Period:** 20260616 – 20260624
**Live trades:** 1229
**Includes 20260624:** True

## Phase524A mandatory

- 1_best_stop_to_stop_reducer: **H_break_exit_and_adx**
- 1_stop_to_stop_baseline: **30**
- 1_stop_to_stop_best: **2**
- 2_5074_baseline: **1**
- 2_5074_best: **0**
- 2_best_5074_reducer: **B_break_prev_exit**
- 3_best_pnl: **118400.0**
- 3_best_pnl_guard: **E_rsi_gt_60**
- 4_best_combined_guard: **E_rsi_gt_60**
- 5_operational_candidate: **True**
- 5_operational_candidates: **['B_break_prev_exit', 'C_break_prev_entry', 'D_break_prev_high', 'E_rsi_gt_60', 'F_adx_gt_25', 'G_break_exit_and_rsi', 'H_break_exit_and_adx', 'I_break_exit_and_high', 'J_break_exit_rsi_adx']**
- 6_phase522_replay_guard_net_best: **A_baseline**
- 6_replay_vs_live_conclusion_changed: **True**
- 7_phase522_guard_unnecessary_was_wrong: **True**
- 8_shadow_candidate: **E_rsi_gt_60**
- baseline_pnl: **-228300.0**
- baseline_stop_to_stop: **30**

## Phase524B mandatory

- 1_best_effect_size: **-0.4718**
- 1_best_separation_feature: **adx14**
- 1_best_separation_score: **0.1857**
- 2_can_separate_rising_vs_bounce: **True**
- 3_momentum_low_misread_evidence: **False**
- 3_stop_low_mfe_count: **130**
- 3_winner_count: **537**
- 4_entry_improvement_candidates: **['adx14', 'spread', 'prior_low_break']**
- 5_next_entry_guard_to_test: **adx14**

Live paper only — no Runtime adoption.