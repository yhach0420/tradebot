# Phase528: Entry Quality Guard (G9) Production Adoption

## Policy

At ENTRY, require **both**:

| Check | Threshold | `reject_reason` |
|-------|-----------|-----------------|
| spread_bps | <= 50 | `entry_quality_guard_spread` |
| update_count_before_entry | <= 5 | `entry_quality_guard_update_count` |

Spread is checked first; update_count is checked only when spread passes.

## Source validation

Phase527 live paper (G9): PnL +34.3k vs baseline -228.3k, PF 1.35, stop_low_mfe 130→24, net improvement +262.6k, no true_breakout winner false blocks.

## Config

```yaml
entry_quality_guard_enabled: true
entry_quality_max_spread_bps: 50.0
entry_quality_max_update_count: 5
```

**Rollback:** `entry_quality_guard_enabled: false`

## Runtime wiring

- `src/small_paper/entry_quality_guard.py` — spread from board payload, update_count from float epoch price ring
- `src/research/exposure_gate.py` — ENTRY reject in PBv2 path
- `src/small_paper/pilot_runner.py` — enrich, reject logging, session summary
- `src/small_paper/discord_message_builder.py` — policy trial lines with spread/update reject counts
- `src/small_paper/live_pipeline_preflight.py` — float timestamp preflight cases

## Ready check

```bash
python kabu_native/scripts/run_phase528_entry_quality_guard_ready.py
```

Verdict when ready: `phase528_entry_quality_guard_runtime_ready`

## Tests

```bash
python -m unittest kabu_native.tests.test_phase528_entry_quality_guard -v
```

## Acceptance review (paper, day+1)

Monitor in session SUMMARY / Discord:

- PnL, PF, stop_low_mfe
- `entry_quality_guard_reject_count`
- `entry_quality_guard_spread_reject_count` / `entry_quality_guard_update_reject_count`
- `reject_reason_counts` (aggregate)
- stop_to_stop chain count
