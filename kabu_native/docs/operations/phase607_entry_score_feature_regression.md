# Phase607 — entry_score_v2 Feature Regression Audit

**Verdict:** `phase607_entry_score_feature_regression_done`

PBv2 source of truth: 6/25 live accepts with entry_score_v2_gate_pass=true (n=70)

## Mandatory answers

### 1_same_calculation_as_625
YES — entry_expectancy_score_shadow.py UNCHANGED since f50c5a7; 70/70 live rows: recompute score == live score (mismatch=0)

### 2_first_differing_feature
NONE on 6/25 PBv2 cohort (score identical). 629/630 OR-only cohort differs: momentum_continuation_score > 0.2546 (no Momentum:low token) or board tertile low (no Board:mid|high) — input distribution, not formula change

### 3_when_feature_generation_changed
No change since f50c5a7 in score or board_imbalance modules (git diff empty)

### 4_commits
196a559 kabutrade0621 last touch entry_expectancy_score_shadow; no diff f50c5a7..HEAD

### 5_score_point_change
625 PBv2 70 rows: delta=0 for all; mean score=3; 629/630 live accepts score 0-2

### 6_same_trend_all_80
YES for 625: all 70 rows score=3, tokens Momentum:low+Board:*; HEAD pbv2 pass=70/70

### 7_rollback_code_location
No score code rollback needed; PBv2 blockers are guard stack + market inputs on 629/630

### 8_minimal_change_to_restore_625_score
None required for score formula. Ensure momentum_continuation_score + entry_order_book_imbalance populated at eval (live_feature_bridge + board_imbalance_shadow unchanged). 629/630 recovery requires favorable momentum/board at entry, not score code revert

### source_of_truth_trace
{'trace_step': 'source_of_truth_chain', 'symbol': '6327.T', 'timestamp': '2026-06-25T09:18:27+09:00', 'step_1_raw_momentum': 0.1762, 'step_2_momentum_tertile': 'low', 'step_3_momentum_token': 'Momentum:low', 'step_4_momentum_points': 2, 'step_5_raw_board': 0.649254, 'step_6_board_tertile': 'high', 'step_7_board_token': 'Board:high', 'step_8_board_points': 1, 'step_9_final_score': 3.0, 'first_function': 'entry_expectancy_score_shadow._score_fields_from_points', 'first_variable': 'momentum_continuation_score', 'head_vs_live_score_delta': 0}

### 629_630_single_feature_rollback_best
{'feature_rollback_id': 'momentum_force_low', 'patch': '{"momentum_continuation_score": "0.20"}', 'target_cohort': '629_630_live_accepts', 'cohort_size': 18, 'pbv2_pass_count': 0, 'note': 'single feature only — no combination'}
