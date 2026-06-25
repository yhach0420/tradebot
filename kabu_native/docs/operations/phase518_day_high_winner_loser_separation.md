# Phase518 — day_high Winner / Loser Separation

**Verdict:** `phase518_day_high_winner_loser_separation_done`
**Period:** 20260529 – 20260622
**Overlay-only trades:** 172 (W=96 L=76)

## Effect size ranking (top 5)

- **spread**: d=-0.3289, sep=0.1053, W_med=63.485, L_med=78.255
- **board_imbalance**: d=-0.2817, sep=0.1667, W_med=0.416666, L_med=0.481532
- **stoch_d**: d=0.2778, sep=0.0625, W_med=91.5404, L_med=90.144
- **rolling_volume_percentile**: d=0.2547, sep=0.1562, W_med=90.0, L_med=77.5
- **minutes_from_open**: d=-0.2014, sep=0.0789, W_med=87.075, L_med=102.175

## Breakout types

- **true_breakout**: n=92, win_rate=0.9457, PnL=583500.0
- **late_breakout**: n=58, win_rate=0.1034, PnL=-160500.0
- **high_chase**: n=22, win_rate=0.1364, PnL=-72200.0

## Mandatory answers

1. **1_best_separating_feature**: spread
2. **2_update_count_effective**: False
3. **3_adx_effective**: False
4. **4_vwap_distance_effective**: False
5. **5_board_imbalance_effective**: True
6. **6_volume_ratio_effective**: False
7. **7_true_breakout_profile**: {'breakout_type': 'true_breakout', 'trade_count': 92, 'win_count': 87, 'loss_count': 5, 'win_rate': 0.9457, 'total_pnl_yen_100': 583500.0, 'avg_pnl_yen_100': 6342.39, 'median_update_count': 4.0, 'median_minutes_from_open': 85.26, 'median_vwap_distance_pct': 3.0195, 'median_adx14': 57.5188, 'median_volume_ratio': 1.0596, 'median_board_imbalance': 0.4167}
8. **8_late_breakout_profile**: {'breakout_type': 'late_breakout', 'trade_count': 58, 'win_count': 6, 'loss_count': 52, 'win_rate': 0.1034, 'total_pnl_yen_100': -160500.0, 'avg_pnl_yen_100': -2767.24, 'median_update_count': 6.0, 'median_minutes_from_open': 140.875, 'median_vwap_distance_pct': 3.4072, 'median_adx14': 54.066, 'median_volume_ratio': 1.0454, 'median_board_imbalance': 0.4815}
9. **9_high_chase_profile**: {'breakout_type': 'high_chase', 'trade_count': 22, 'win_count': 3, 'loss_count': 19, 'win_rate': 0.1364, 'total_pnl_yen_100': -72200.0, 'avg_pnl_yen_100': -3281.82, 'median_update_count': 2.0, 'median_minutes_from_open': 63.575, 'median_vwap_distance_pct': 2.8792, 'median_adx14': 51.7442, 'median_volume_ratio': 1.023, 'median_board_imbalance': None}
10. **10_late_breakout_excludable_at_entry**: True
11. **11_high_chase_excludable_at_entry**: False
12. **12_simple_rule_improvement_possible**: True
13. **13_next_refinement_candidates**: ['breakout_type gate (true_breakout vs late/high_chase)']
