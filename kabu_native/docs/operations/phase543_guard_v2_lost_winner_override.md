# Phase543A — Guard v2 Lost Winner / Override Design

**Verdict:** `phase543_guard_v2_lost_winner_override_done`
**Period:** 20260616 – 20260625
**Trades:** 1309

## Mandatory answers

- **1_why_G_A_blocks_winners:** High-ADX filter removes trending winners; top cluster=volume_surge. adx14(d=-3.0133), minutes_from_open(d=0.9063), update_count_before_entry(d=0.2776)
- **2_why_G_B_blocks_winners:** ADX35+FIVE50 blocks upper 5min-range trend continuations; cluster=volume_surge. five_min_position(d=-1.6476), moving_average_position(d=-0.4942), minutes_from_open(d=0.4327)
- **3_why_G_C_blocks_winners:** Stricter ADX30+FIVE50 drops strong-trend winners; cluster=volume_surge. five_min_position(d=-1.6054), moving_average_position(d=-0.4616), adx14(d=-1.0719)
- **4_lost_winner_common_traits:** ['G_A:adx14', 'G_A:minutes_from_open', 'G_B:five_min_position', 'G_C:five_min_position', 'G_A:update_count_before_entry']
- **5_lost_big_winner_common_traits:** high ADX + volume_surge/day_leader clusters dominate blocked big winners
- **6_best_override_candidates:** ['O11_board_vol_high', 'O10_vol_or_high_update', 'O2_volume_pct', 'O8_vwap_positive']
- **7_override_recovers_winners:** True
- **8_override_reintroduces_too_much_mfe0:** True
- **9_best_guard_override:** G_C+O11_board_vol_high
- **10_most_explainable_guard_override:** G_C+O10_vol_or_high_update
- **11_shadow_forward_candidates:** ['G_A+O1_board_imbalance', 'G_A+O6_prior_high_break', 'G_A+O12_day_leader_proxy', 'G_B+O1_board_imbalance', 'G_C+O1_board_imbalance']
- **12_production_adoption_candidate:** False
- **13_next_phase:** Phase543B: forward-shadow best Guard+Override on new live days.
- **best_recovered_big_winners:** 91
- **best_reintroduced_mfe0:** 236

Research only. No Runtime adoption.
