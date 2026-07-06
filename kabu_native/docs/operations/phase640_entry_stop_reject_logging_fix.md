# Phase640: Entry Stop Reject Logging Fix

## Problem

On `am_pm_entry_stop` and `outside_refresh_universe` pre-gate branches, Stage1 returned
without assigning `ref_now`. Stage6 audit then raised `UnboundLocalError`, caught upstream
as `push_unexpected`. Candidate events were written, but **rejected events and reject rows
were not**.

Historical evidence (2026-07-01 AM live session `live_session_080616`):

- `am_pm_entry_stop` **candidate** events: **29**
- matching **rejected** events: **0**
- `push_unexpected` errors referencing `ref_now`: recorded in `errors.jsonl`

## Fix (logging only — no ENTRY/EXIT/PBv2/OR decision changes)

1. **Stage1** (`_stage1_evaluate_freshness`): assign
   `ref_now = _replay_reference_now(ctx, enriched) or datetime.now(JST)` before returning
   on pre-gate short-circuits.
2. **Stage6** (`_stage6_record_candidate`): remove preserved `UnboundLocalError`; wrap
   audit / `record_symbol_eval` in try/except.
3. **Stage6 reject** (`_stage6_record_reject`): wrap reject row / event writers in
   try/except; increment `entry_stop_reject_logging_recovered_count` on success for
   pre-gate reasons.
4. **Summary**: emit `entry_stop_reject_logging_recovered_count` and `logging_error_count`.

Accepted count and PnL paths are unchanged. New reject rows/events after entry stop are
**expected diffs** vs pre-Phase640 artifacts.

## Summary fields

| Field | Meaning |
|-------|---------|
| `entry_stop_reject_logging_recovered_count` | Pre-gate rejects successfully logged this session |
| `logging_error_count` | Audit/reject writer exceptions swallowed (pipeline continues) |

## Audit

```bash
python scripts/run_phase640_entry_stop_reject_logging_fix.py
python scripts/run_phase640_entry_stop_reject_logging_fix.py --skip-parity
```

Full-day accepted parity (Phase630 baseline, 4 fixture days) runs by default; use
`--skip-parity` for a quick audit.

## Tests

```bash
python -m pytest tests/test_phase640_entry_stop_reject_logging_fix.py -q
```

## Verdict

`phase640_entry_stop_reject_logging_fix_done`
