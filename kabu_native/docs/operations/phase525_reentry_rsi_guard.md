# Phase525: Re-entry RSI guard runtime adoption

## Policy

After a **stop_hit** exit on the same symbol, the next ENTRY requires **RSI14 > 60**.
First entries are unaffected (re-entry only).

| Item | Value |
|------|-------|
| `reject_reason` | `reentry_rsi_guard_below60` |
| Config enable | `reentry_rsi_guard_enabled: true` |
| Threshold | `reentry_rsi_guard_threshold: 60.0` |
| Rollback | `reentry_rsi_guard_enabled: false` |

## Source validation

Phase524 live audit (20260616–20260624): guard **E_rsi_gt_60** was best net PnL vs baseline.

## Runtime wiring

- `src/small_paper/reentry_rsi_guard.py` — guard state, RSI enrichment, `record_exit` on observer exits
- `src/research/exposure_gate.py` — ENTRY reject in `evaluate_entry`
- `src/small_paper/pilot_runner.py` — enrich, reject logging, session summary
- `src/small_paper/live_pipeline_preflight.py` — float epoch price-ring preflight cases

## Preflight / ready check

```bash
python kabu_native/scripts/run_phase525_reentry_rsi_guard_runtime_ready.py
```

Verdict when ready: `phase525_reentry_rsi_guard_runtime_ready`

## Tests

```bash
python -m unittest kabu_native.tests.test_phase525_reentry_rsi_guard -v
```
