# Phase435 — Runtime Entry Path Trace Report

Generated: 2026-06-18T20:39:19+09:00
**Verdict:** `phase314_misunderstood`

## Part A — Code Audit (summary)

- Final accept: `research/exposure_gate.py::ExposureGate.evaluate_entry`
- `entry_score_v2_min`: **3** — entry_expectancy_score_v2 is sum of SCORE_POINTS_V2 token hits; max=3 (Momentum:low=2 + Board:mid=1). Gate requires momentum_low_required_for_v2 AND score>=entry_score_v2_min.
- Momentum+Board role: REQUIRED: momentum_low_required_for_v2 (Momentum:low token) is mandatory when entry_score_v2_min>0. Board:mid is not independently required but score=3 at min=3 implies both tokens.
- `entry_expectancy_score_v2>=5` is gate: **False**
- `candidate_rank_score`: Ranking only within EntryScanController scan batch; does NOT grant entry permission. Permission is ExposureGate accept; rank_score breaks ties for max_entries_per_scan.
- `max_entries_per_scan`: 1

## Part B — 6976.T

All 7 entries: **A_momentum_low_board_mid_gate for all 7**

Why 7 entries: 6976 passed ExposureGate 7 times (Momentum:low+Board:mid, v2=3) on separate push cycles; no_overlap_replace allows re-entry after prior position closed; 4 became stop_hit.

## Part C — 89 accepted distribution

{'A_momentum_low_board_mid_gate': 89}

Loss driver: **A_momentum_low_board_mid_gate**

## Part D/E — Phase314 consistency

- Phase434 mismatch: Phase434 used wrong fields (entry_momentum_score/entry_imbalance_percentile). Runtime uses momentum_continuation_score + entry_order_book_imbalance tertiles — all 7 ARE Momentum:low+Board:mid.
- Phase314 wrong?: Phase314 runtime is correct; Phase434 audit metric was wrong. Docs/architecture are correct.

### Guards (6976)

- Pullback: Pullback guard blocks only when entry_rise_5min_pct<0 AND entry_vwap_dev_pct<0 on dynamic40. 6976 entries had negative 5m rise but positive vwap_dev → guard did not fire.
- Near day high: Near-day-high guard blocks when day_high_distance<=1.5% AND entry_momentum<0.30. 6976 entries were often >1.5% below day high (entry_near_day_high_pct ~1.7-5%) or momentum not low enough for guard field.

## Mandatory answers

1. 6976 passed ExposureGate 7 times (Momentum:low+Board:mid, v2=3) on separate push cycles; no_overlap_replace allows re-entry after prior position closed; 4 became stop_hit.

2. Phase434 used wrong fields (entry_momentum_score/entry_imbalance_percentile). Runtime uses momentum_continuation_score + entry_order_book_imbalance tertiles — all 7 ARE Momentum:low+Board:mid.

3. Yes as gate: entry_expectancy_score_v2>=entry_score_v2_min WITH momentum_low_required. No: ge5 is v1 shadow flag; v2 max is 3.

4. entry_expectancy_score_v2 is sum of SCORE_POINTS_V2 token hits; max=3 (Momentum:low=2 + Board:mid=1). Gate requires momentum_low_required_for_v2 AND score>=entry_score_v2_min.

5. A_momentum_low_board_mid_gate for all 7

6. {'A_momentum_low_board_mid_gate': 89}

7. A_momentum_low_board_mid_gate

8. Phase314 runtime is correct; Phase434 audit metric was wrong. Docs/architecture are correct.

9. Not entry gate logic — fix audit classification fields; consider guard tuning (pullback vwap sign, same-symbol stop cooldown) not score_v2 path.

10. ['Pullback guard: require negative vwap_dev OR rising-from-low pattern on downtrend', 'Same-symbol cooldown after stop_hit (Phase434 counterfactual +97.5k)', 'High-notional price band cap for 10k+ symbols', 'Audit tooling: use active_score_tokens_v2 not percentile proxies']

## Artifacts
- `phase435_entry_path_trace_6976.csv`
- `phase435_entry_path_distribution.csv`
- `phase435_entry_code_path_audit.json`
- `phase435_entry_path_loss_attribution.csv`
- `phase435_entry_path_summary.json`