# Phase544 — ENTRY Feature Attribution

**Verdict:** `phase544_entry_feature_attribution_done`
**Period:** 20260616 – 20260625
**Trades:** 1309
**Baseline PnL:** -227520.0

## Top threshold candidates

- `day_return_rank` ge 50.21: PnL=170680.0 MFE0=178 big_win=108 retention=0.4523
- `volume_percentile` ge 50.0: PnL=143950.0 MFE0=342 big_win=121 retention=0.7021
- `board_update_frequency` ge 0.0595: PnL=97980.0 MFE0=81 big_win=57 retention=0.2261
- `five_min_position` le 54.5455: PnL=94050.0 MFE0=140 big_win=66 retention=0.3132
- `adx14` le 22.2222: PnL=71900.0 MFE0=181 big_win=45 retention=0.3377
- `minutes_from_open` le 311.53: PnL=58730.0 MFE0=273 big_win=148 retention=0.6769
- `spread_bps` ge 47.76: PnL=49700.0 MFE0=78 big_win=70 retention=0.2277
- `volume_ratio` le 1.1494: PnL=43980.0 MFE0=289 big_win=147 retention=0.683

## Mandatory answers

- **1_top_winner_separator:** day_high_distance_pct
- **1_importance_winner:** minutes_from_open
- **2_top_mfe0_separator:** update_count_before_entry
- **2_importance_mfe0:** board_update_frequency
- **3_top_big_winner_separator:** board_update_frequency
- **3_importance_big_winner:** board_imbalance
- **4_top_stop_low_mfe_separator:** update_count_before_entry
- **4_importance_stop_low_mfe:** volume
- **5_top_no_progress_separator:** vwap_distance_pct
- **6_entry_misrecognition:** 高ADX・高five_min_position・弱boardの追いかけENTRYと、volume_surge/day_leader型の強い動きを同一ENTRYパスで処理している
- **7_mfe0_primary_cause:** update_count_before_entry: MFE0群はwinner群よりADX/five_min_positionが高く、board_imbalance・volume_percentileが低い（モメンタム枯渇後の遅延ENTRY）
- **8_big_winner_common_traits:** board_imbalance≥0.55、volume_percentile≥70、high_update_recent=True、five_min_position≤50、day_return_rank上位
- **9_entry_improvement_features:** ['day_return_rank', 'volume_percentile', 'board_update_frequency', 'five_min_position', 'adx14']
- **10_shadow_entry_candidates:** ['day_return_rank', 'volume_percentile', 'tick_speed']
- **11_runtime_candidate:** False
- **11_runtime_note:** Runtime変更・採用は禁止。A分類は研究上の採用候補であり本番Runtimeには進めない
- **12_next_phase:** phase545_entry_filter_shadow_design
- **mfe0_median_adx_note:** {'feature': 'update_count_before_entry', 'cohort': 'mfe0', 'count': 452, 'mean': 4.291284, 'median': 1.0, 'p25': 1.0, 'p75': 5.0, 'missing_rate': 0.0354, 'cohens_d_vs_rest': -0.1849, 'separation_score_vs_rest': 0.2051}
- **big_winner_top_features:** ['board_imbalance', 'volume', 'board_update_frequency', 'spread_bps', 'minutes_from_open']

## Next phase

Guard/Override 研究は Phase543 で完了。ENTRY filter shadow 設計へ (`phase545_entry_filter_shadow_design`).

