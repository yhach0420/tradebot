# Phase523 — Re-Entry Definition + Overlay Edge Reality Audit

**Verdict:** `phase523_reentry_definition_overlay_edge_reality_audit_done`
**Period:** 20260529 – 20260624
**Live trades loaded:** 2913
**Replay trades:** 440

## Part A — why Phase522 showed 0 chains

- 5074 live data available: **True**
- Period gap: 20260624 not in Phase522 replay pool

1. Phase522 PERIOD_END=20260622 excluded 20260624
2. Phase522 used replay_cap only (no live overlap_replaced structural exits)
3. Phase522 D1 required stop_hit on trades[i] and trades[i+2] with any trade[i+1] between
4. CAP replay collapses same-symbol churn differently than live AM sessions

## Part A mandatory

- 1_phase522_zero_reason: **['Phase522 PERIOD_END=20260622 excluded 20260624', 'Phase522 used replay_cap only (no live overlap_replaced structural exits)', 'Phase522 D1 required stop_hit on trades[i] and trades[i+2] with any trade[i+1] between', 'CAP replay collapses same-symbol churn differently than live AM sessions']**
- 2_5074_data_available: **True**
- 2_5074_live_stop_chain: **True**
- 3_exit_reason_classification_gap: **overlap_replaced_review masks structural stop_hit in live**
- 4_period_out_of_range: **20260624 not in Phase522 replay pool**
- 5_relaxed_d1_live: **79**
- 5_relaxed_d1_replay: **0**
- 5_relaxed_d4_live: **130**
- 6_reentry_guard_revalidation_needed: **True**
- stop_to_stop_live: **{'follow_up_class': 'stop_to_stop', 'trade_count': 134, 'total_pnl_yen_100': -743139.63, 'profit_factor': 0.0016, 'win_rate': 0.0075, 'avg_mfe_pct': 0.1969, 'avg_mae_pct': -1.3056}**

## Part B mandatory

- 10_shadow_continue: **neither**
- 1_g3_jaccard_top10: **0.4286**
- 1_g3_top_profit_same_as_pbv2: **True**
- 2_or_jaccard_top10: **0.3333**
- 2_or_top_profit_same_as_pbv2: **True**
- 3_g3_rising_capture_higher: **True**
- 4_or_rising_capture_higher: **True**
- 5_g3_top10_accidental: **False**
- 6_or_top10_accidental: **False**
- 7_top10_exclusion_still_viable: **False**
- 8_g3_coexistence: **B_same_symbol_earlier_entry**
- 9_or_coexistence: **B_same_symbol_earlier_entry**
- adopt_not_allowed: **True**
- baseline_pnl: **214959.61**
- overlay_unique_symbols_6976_excluded: **{'O_R003_OR': ['6227', '6323', '3891', '6656', '5337', '4889', '6507', '4100', '6838', '4078'], 'G3_G4': ['6323', '3891', '3687', '5337', '5985', '6597', '7717', '9256']}**

Research only — no Runtime adoption.