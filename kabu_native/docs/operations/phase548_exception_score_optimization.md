# Phase548 — Exception Score Optimization

**Verdict:** `phase548_exception_score_optimization_done`
**Runtime変更:** なし / **採用:** なし

## Score components (max 9)

- `liquidity_burst_high` (liquidity_burst >= p75): +2
- `vwap_recovery_fast` (vwap_recovery_min <= median): +2
- `update_count_high` (update_count_before_entry >= median): +1
- `relative_volume_high` (relative_volume >= p75): +1
- `day_leader` (day_return_rank <= 20): +1
- `board_strong` (board_imbalance >= 0.60): +1
- `open_strength` (open_strength == true): +1

## Ranking

- #1 SCORE>=3: PnL=167880.0 PF=1.18 rec_big=15
- #2 E4: PnL=167680.0 PF=1.1943 rec_big=5
- #3 SCORE>=5: PnL=146980.0 PF=1.178 rec_big=2
- #4 SCORE>=4: PnL=126580.0 PF=1.1482 rec_big=3
- #5 E10: PnL=113580.0 PF=1.1339 rec_big=2
- #6 E5: PnL=129580.0 PF=1.1449 rec_big=14
- #7 E1: PnL=107380.0 PF=1.126 rec_big=2
- #8 V6: PnL=136880.0 PF=1.1677 rec_big=0

## Mandatory answers

- **1_best_score_composition:** {'liquidity_burst_high': 2, 'vwap_recovery_fast': 2, 'update_count_high': 1, 'relative_volume_high': 1, 'day_leader': 1, 'board_strong': 1, 'open_strength': 1}
- **2_best_score_threshold:** 3
- **3_rescued_winner_count:** 60
- **4_rescued_big_winner_count:** 15
- **5_reintroduced_loser_count:** 70
- **6_reintroduced_mfe0_count:** 51
- **7_pnl_yen_100:** 167880.0
- **8_profit_factor:** 1.18
- **9_retention:** 0.6295
- **10_beats_e4:** False
- **11_shadow_candidates:** ['E4']
- **12_runtime_candidate:** False
- **13_next_phase:** phase549_entry_cluster_shadow_monitor
- **score_thresholds_used:** {'liquidity_burst_p75': 0.052267, 'price_acceleration_p75': 0.7546, 'relative_volume_p75': 1.130435, 'vwap_recovery_min_median': 14.94165, 'update_count_median': 1.0}
- **score_beats_e4_count:** 0
