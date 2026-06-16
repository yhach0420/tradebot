# Phase410 — Duplicate Re-entry / Boundary Shadow Interaction Audit

Generated: 2026-06-16T20:46:50+09:00
Audit day: 20260616
Verdict: **PASS**

## Session overview

| Session | Trades | Symbols | overlap_replaced_review | avg_hold_sec |
|---------|--------|---------|-------------------------|--------------|
| AM | 391 | 8 | 375 | 60.69663673401535 |
| PM | 383 | 9 | 372 | 73.68442022976501 |

## Mandatory answers

1. **Abnormal trade_count cause:** same_symbol_overlap_replaced_review_churn
2. **overlap_replaced_review = same-symbol replace:** True
3. **Phase409 silent cause:** hold_too_short_plus_phase409_exit_time_mapping_gap
4. **day_count=0:** True — day_count counts trades with successful shadow eval on FORWARD_PERIOD_START+; 6/16 had 774 structural trades but phase409 wrote 0 rows because load path did not map close_time->exit_time before prepare_corrected_trade_context. Even after fix, boundary_eligible would be ~37 / 774 due to median hold ~60-74s vs 5min bucket.
5. **Policy A trade_count:** 394 (baseline 774)
6. **Policy A PnL/PF/maxDD:** {'pnl': 23200.0, 'pf': 1.169, 'maxdd': 32300.0}
7. **Fix candidates:** fix_phase409_close_time_to_exit_time_mapping, same_symbol_open_reentry_reject_or_cooldown_research, no_overlap_replace_counterfactual_continue, do_nothing_on_exit_policy_until_forward_shadow_review

## Overlap replace cause breakdown

- total events: 751
- same_symbol chain: 747
- verdict: intended_same_symbol_replace_not_cap_forced
- observer spec: pilot_runner closes same-symbol open via close_for_overlap before register_entry

## Boundary eligibility

- eligible: 37 / 774
- boundary hit: 33
- skip reasons: {'hold_too_short': 737, 'logged': 33, 'no_boundary_trigger': 4}

## Counterfactual policies

| Policy | trades | PnL | PF | maxDD | avg_hold | boundary_eligible | would_trigger |
|--------|--------|-----|----|-------|----------|-------------------|---------------|
| baseline | 774 | 3300.0 | 1.0125 | 36000.0 | 69.97 | 37 | 33 |
| same_symbol_open_reentry_reject | 394 | 23200.0 | 1.169 | 32300.0 | 84.95 | 27 | 24 |
| same_symbol_cooldown_5min | 91 | 6200.0 | 1.1699 | 18500.0 | 132.98 | 12 | 10 |
| same_symbol_cooldown_15min | 48 | -13400.0 | 0.6869 | 19200.0 | 143.83 | 10 | 8 |
| no_overlap_replace | 394 | 23200.0 | 1.169 | 32300.0 | 84.95 | 27 | 24 |

## Top churn symbols (AM)

| symbol | entries | overlap | median_hold | pnl |
|--------|---------|---------|-------------|-----|
| 464A.T | 188 | 185 | 17.0 | 3600.0 |
| 4047.T | 76 | 74 | 37.5 | 3000.0 |
| 3687.T | 67 | 63 | 26.0 | 16500.0 |
| 6264.T | 46 | 44 | 10.5 | -400.0 |
| 6981.T | 6 | 5 | 10.5 | 2000.0 |
| 6227.T | 4 | 2 | 78.5 | 5000.0 |
| 5367.T | 3 | 2 | 16.0 | -300.0 |
| 4392.T | 1 | 0 | 70.0 | -2000.0 |

- Runtime / YAML / Entry / Exit unchanged
