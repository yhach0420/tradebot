# Phase607B — PBv2 Eval Score Distribution Audit

**Verdict:** `phase607b_pbv2_eval_score_distribution_audit_done`

### 1_score3_exists_629_630
YES — score>=3 eval candidates: 46509 (accept=27654, reject=18855)

### 2_why_not_accept_if_score3
{'high_drift_pullback': 12313, 'near_day_high_low_momentum_dynamic40_guard': 3502, 'entry_quality_guard_update_count': 1747, 'late_chase_guard': 803, 'entry_quality_guard_spread': 490}

### 3_if_no_score3_which_axis
score3 EXISTS (46509); core combo momentum_low+board_mid/high: BAD=46509 vs 625=28345

### 4_distribution_vs_625
[{'label': '625_GOOD', 'n_eval': 47287, 'momentum_median': 0.25, 'momentum_mean': 0.1731, 'board_median': 0.5093, 'board_mean': 0.5108, 'score3_count': 28345, 'score3_pct': 0.5994, 'score3_accept': 14803, 'score3_accept_rate': 0.5222, 'mb_core_pct': 0.5994, 'score2_pct': 0.1213}, {'label': '629_630_BAD', 'n_eval': 64647, 'momentum_median': 0.0738, 'momentum_mean': 0.1354, 'board_median': 0.5381, 'board_mean': 0.5456, 'score3_count': 46509, 'score3_pct': 0.7194, 'score3_accept': 27654, 'score3_accept_rate': 0.5946, 'mb_core_pct': 0.7194, 'score2_pct': 0.0732}, {'label': 'delta_BAD_minus_GOOD', 'n_eval': 17360, 'momentum_median': -0.1762, 'board_median': 0.028800000000000048, 'score3_pct': 0.12, 'mb_core_pct': 0.12}]

### 5_score2_near_miss_volume
score2 count BAD=4732; GOOD625=5734

### 6_score2_follow_through
score2 with rolling_mfe>1%: 146/4732 on BAD days

### 7_missed_winners_max_score
[{'day': '20260630', 'symbol': '7352.T', 'intraday_range_pct_max': 15.102, 'max_up_pct_proxy': 5.4852, 'score_max': 3, 'score_max_time': '2026-06-30T11:19:56+09:00', 'momentum_at_max_score': 0.0113, 'board_at_max_score': 0.875476, 'first_blocker_at_max_score': 'pbv2_accept', 'accepted_any': True, 'missed_reason': 'pbv2_accept', 'pbv2_eval_count': 541}, {'day': '20260630', 'symbol': '6327.T', 'intraday_range_pct_max': 12.6087, 'max_up_pct_proxy': 3.3289, 'score_max': 3, 'score_max_time': '2026-06-30T10:19:28+09:00', 'momentum_at_max_score': 0.2539, 'board_at_max_score': 0.447115, 'first_blocker_at_max_score': 'near_day_high_low_momentum_dynamic40_guard', 'accepted_any': True, 'missed_reason': 'near_day_high_low_momentum_dynamic40_guard', 'pbv2_eval_count': 605}, {'day': '20260629', 'symbol': '7352.T', 'intraday_range_pct_max': 12.2892, 'max_up_pct_proxy': 6.9869, 'score_max': 3, 'score_max_time': '2026-06-29T15:16:23+09:00', 'momentum_at_max_score': 0.2504, 'board_at_max_score': 0.670692, 'first_blocker_at_max_score': 'pbv2_accept', 'accepted_any': True, 'missed_reason': 'pbv2_accept', 'pbv2_eval_count': 723}, {'day': '20260629', 'symbol': '4265.T', 'intraday_range_pct_max': 10.0, 'max_up_pct_proxy': 6.4356, 'score_max': 3, 'score_max_time': '2026-06-29T15:15:21+09:00', 'momentum_at_max_score': 0.25, 'board_at_max_score': 0.975155, 'first_blocker_at_max_score': 'near_day_high_low_momentum_dynamic40_guard', 'accepted_any': True, 'missed_reason': 'near_day_high_low_momentum_dynamic40_guard', 'pbv2_eval_count': 353}, {'day': '20260629', 'symbol': '6620.T', 'intraday_range_pct_max': 9.8837, 'max_up_pct_proxy': 5.9765, 'score_max': 3, 'score_max_time': '2026-06-29T14:46:45+09:00', 'momentum_at_max_score': 0.25, 'board_at_max_score': 0.808511, 'first_blocker_at_max_score': 'near_day_high_low_momentum_dynamic40_guard', 'accepted_any': True, 'missed_reason': 'near_day_high_low_momentum_dynamic40_guard', 'pbv2_eval_count': 348}]

### 8_pbv2_too_strict_for_regime
PARTIAL — score3 candidates exist but guards (near_day_high, cluster, momentum path) block; also momentum_high regime reduces score3 formation vs 625

### 9_impl_config_bug_remaining
NO score calc bug; guard stack + regime distribution

### 10_minimal_relax_next
high_drift_off
