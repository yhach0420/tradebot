# Phase483 — PBv2 stop_low_mfe Root Cause Audit

**Verdict:** `entry_root_cause_found`
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | stop_low_mfe件数 | **42** |
| 2 | stop_low_mfe合計損失 | **-210100.1** |
| 3 | 主因 | **late_chase_after_rally_vwap_trap** |
| 4 | 最分離ENTRY特徴 | **momentum_continuation_score** (d=0.3758) |
| 5 | strong_winnerと最違い | **momentum_continuation_score** |
| 6 | Board効果 | **{'slm_mid_share': 0.9762, 'sw_mid_share': 0.9067, 'verdict': 'board_does_not_prevent_slm'}** |
| 7 | cutoff甘さ | **{'slm_mean_momentum': 0.1648, 'sw_mean_momentum': 0.1229, 'cutoff': 0.2546, 'slm_near_cutoff_count': 25, 'verdict': True}** |
| 8 | Late Chase取逃 | **{'would_late_chase_block': 0, 'all_passed': True}** |
| 9 | Drift/Shape gap | **{'would_high_drift_block': 0, 'would_weak_shape_block': 0, 'all_passed': True}** |
| 10 | 最良2条件 | **{'pattern_id': 'P2_r10_high_update_age', 'conditions': 'r10>0.2462@p40 AND high_update_age>16.7300@p40', 'separation_score': 0.1733}** |
| 11 | blocked_slm | **20** |
| 12 | blocked_winners | **20** |
| 13 | expected_delta | **-12989.9** |
| 14 | 6976 | **{'slm_count': 1, 'slm_pnl': -21000.0, 'best_pattern_blocked': 6}** |
| 15 | 4062 | **{'slm_count': 1, 'slm_pnl': -21500.0, 'best_pattern_blocked': 4}** |
| 16 | Runtime候補 | **False** |
| 17 | 次アクション | ['Verdict: entry_root_cause_found', 'Primary root cause: late_chase_after_rally_vwap_trap', 'Entry separator: momentum_continuation_score (d=0.3758)', 'Design new entry feature or tighten guard — replay required'] |

## Top patterns

- **P1_r10**: sep 0.2095 slm 20 sw 20 Δ -12989.9
- **P2_r10_high_update_age**: sep 0.1733 slm 14 sw 12 Δ -38090.53
- **P2_r10_day_high_distance**: sep 0.1495 slm 13 sw 12 Δ -42990.65
- **P2_momentum_continuation_score_vwap_part**: sep 0.1447 slm 24 sw 32 Δ -48098.89
- **P2_r10_r30**: sep 0.14 slm 7 sw 2 Δ 20520.62
- **P2_r10_board_imbalance**: sep 0.1372 slm 8 sw 4 Δ 51990.8
- **P2_momentum_continuation_score_r10**: sep 0.1333 slm 14 sw 15 Δ 7520.73
- **P1_vwap_part**: sep 0.1314 slm 24 sw 33 Δ -128798.78

**判定:** `entry_root_cause_found`
