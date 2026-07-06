# Phase635: PBv2-only Rise5 Shadow Guard

## Purpose

Record counterfactual outcomes for Phase634 `pbv2_only_rise5_cap_p95` without blocking live/paper ENTRY.

## Config (production YAML)

```yaml
pbv2_rise5_shadow_enabled: true
pbv2_rise5_shadow_threshold_pct: 1.84
pbv2_rise5_shadow_apply_pool: PBV2_ONLY
```

Rollback: `pbv2_rise5_shadow_enabled: false`

## Runtime hooks

| Stage | Location | Behavior |
|---|---|---|
| Accept | `pilot_runner._execute_accepted_entry` | After `entry_type` set, before `gate.record_accepted` |
| Exit | `observer_position_tracker` | `enrich_exit_pbv2_rise5_shadow_fields` on observer_exit |
| Summary | `_pbv2_rise5_shadow_summary_fields` | Session / AM-PM bucket aggregates |
| Discord | `format_pbv2_rise5_shadow_discord_lines` | `[PBv2 Rise5 Shadow]` block |

## Event fields

**Accepted / rejected audit:**
- `pbv2_rise5_shadow_block`
- `pbv2_rise5_shadow_reason`
- `pbv2_rise5_value`
- `pbv2_rise5_threshold`
- `pbv2_rise5_shadow_apply_pool`

**Observer exit (blocked trade outcomes):**
- `shadow_blocked_pnl_yen_100`
- `shadow_blocked_mfe`
- `shadow_blocked_mae`
- `pbv2_rise5_shadow_pnl_yen_100`
- `pbv2_rise5_shadow_delta_yen`

## Rules

- `entry_type == OR_OVERLAY` → shadow not applied (OR unchanged)
- `entry_rise_5min_pct > threshold` → `pbv2_rise5_shadow_block=true` (logging only)
- Missing rise5 → fail-open (no shadow block)
- ENTRY gate / EXIT policy unchanged

## Validation

```bash
python -m unittest tests.test_phase635_pbv2_rise5_shadow
python scripts/run_phase635_pbv2_rise5_shadow.py
python scripts/check_live_pipeline_preflight.py
```

Parity CI (`scripts/check_runtime_parity.py`): accepted count must match baseline (shadow does not filter).

## Artifacts

`results/reports/phase635_pbv2_rise5_shadow/`
