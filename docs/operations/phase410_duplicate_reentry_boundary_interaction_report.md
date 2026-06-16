# Phase410 — Duplicate Re-entry / Boundary Shadow Interaction Audit

Generated: 2026-06-16T20:12:52+09:00
Audit day: 20260616
Verdict: **PASS**

## Session overview

| Session | Trades | Symbols | overlap_replaced_review | avg_hold_sec |
|---------|--------|---------|-------------------------|--------------|
| AM | 0 | 0 | 0 | None |
| PM | 0 | 0 | 0 | None |

## Mandatory answers

1. **Abnormal trade_count cause:** mixed
2. **overlap_replaced_review = same-symbol replace:** True
3. **Phase409 silent cause:** hold_too_short_plus_phase409_exit_time_mapping_gap
4. **day_count=0:** True — day_count counts trades with successful shadow eval on FORWARD_PERIOD_START+; 6/16 had 774 structural trades but phase409 wrote 0 rows because load path did not map close_time->exit_time before prepare_corrected_trade_context. Even after fix, boundary_eligible would be ~0 / 0 due to median hold ~60-74s vs 5min bucket.
5. **Policy A trade_count:** 0 (baseline 0)
6. **Policy A PnL/PF/maxDD:** {'pnl': 0, 'pf': None, 'maxdd': 0.0}
7. **Fix candidates:** fix_phase409_close_time_to_exit_time_mapping, same_symbol_open_reentry_reject_or_cooldown_research, no_overlap_replace_counterfactual_continue, do_nothing_on_exit_policy_until_forward_shadow_review

## Overlap replace cause breakdown

- total events: 0
- same_symbol chain: 0
- verdict: intended_same_symbol_replace_not_cap_forced
- observer spec: pilot_runner closes same-symbol open via close_for_overlap before register_entry

## Boundary eligibility

- eligible: 0 / 0
- boundary hit: 0
- skip reasons: {}

## Counterfactual policies

| Policy | trades | PnL | PF | maxDD | avg_hold | boundary_eligible | would_trigger |
|--------|--------|-----|----|-------|----------|-------------------|---------------|
| baseline | 0 | 0 | None | 0.0 | 0.0 | 0 | 0 |
| same_symbol_open_reentry_reject | 0 | 0 | None | 0.0 | 0.0 | 0 | 0 |
| same_symbol_cooldown_5min | 0 | 0 | None | 0.0 | 0.0 | 0 | 0 |
| same_symbol_cooldown_15min | 0 | 0 | None | 0.0 | 0.0 | 0 | 0 |
| no_overlap_replace | 0 | 0 | None | 0.0 | 0.0 | 0 | 0 |

## Top churn symbols (AM)

| symbol | entries | overlap | median_hold | pnl |
|--------|---------|---------|-------------|-----|

- Runtime / YAML / Entry / Exit unchanged
