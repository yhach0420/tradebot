# Phase644c: Order Latency Trace Root Cause Audit

Research audit for missing `order_latency_dryrun_trace.jsonl` in live sessions.

## Run

```bash
python scripts/run_phase644c_order_latency_trace_root_cause_audit.py
python -m pytest tests/test_phase644c_order_latency_trace_root_cause_audit.py -q
```

## Root cause (2026-07-06 sessions)

When `live_order_adapter_enabled=true` (production YAML), `_execute_accepted_entry` returned before
`_maybe_record_live_order_wiring_entry` because wiring was behind `_legacy_live_order_hooks_enabled`.

Trace session was initialized (`order_latency_dryrun_trace_enabled=true` in summary) but `_emit` never ran → **sample_count=0**, no JSONL file.

**Fix:** Call `_maybe_record_live_order_wiring_entry` before the legacy guard return in `pilot_runner._execute_accepted_entry`. Emit rejects via `finish_reject` in `_stage6_record_reject`.

## Existing sessions

Cannot backfill trace rows without re-running/replaying the session.

## Verdict

`phase644c_order_latency_trace_root_cause_done`
