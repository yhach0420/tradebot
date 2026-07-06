# Phase650: PBv2 Flat-band Guard Shadow

Shadow-only implementation of Phase649 `flat_plus_overheat` guard.
Does **not** block ENTRY; records counterfactual outcomes on live/paper sessions.

## Condition (`flat_plus_overheat`)

Block shadow when:

1. **flat_band_narrow:** `0 <= rise5 < 0.5%` AND `-0.5 <= rise10 <= 0.5%` (requires both values)
2. **OR overheat:** `rise5 > 2.0%`

Missing `rise10` → flat branch skipped. Missing `rise5` → no block.

## YAML (production shadow config)

```yaml
pbv2_flat_band_shadow_enabled: true
pbv2_flat_band_shadow_apply_pool: PBV2_ONLY
pbv2_flat_band_shadow_rise5_flat_min_pct: 0.0
pbv2_flat_band_shadow_rise5_flat_max_pct: 0.5
pbv2_flat_band_shadow_rise10_flat_min_pct: -0.5
pbv2_flat_band_shadow_rise10_flat_max_pct: 0.5
pbv2_flat_band_shadow_overheat_rise5_pct: 2.0
```

Rollback: `pbv2_flat_band_shadow_enabled: false`

## Module

`src/small_paper/pbv2_flat_band_guard_shadow.py`

Hooks:

- `pilot_runner._execute_accepted_entry` — after rise5 shadow, before `gate.record_accepted`
- `observer_position_tracker` — exit enrichment
- Summary + Discord via `PbV2FlatBandShadowCounters.summary_fields()`

## Run validation

```bash
python -m pytest tests/test_phase650_pbv2_flat_band_shadow.py -q
python scripts/run_phase650_pbv2_flat_band_shadow.py
```

## Artifacts

```
results/reports/phase650_pbv2_flat_band_shadow/
  phase650_report.json
  phase650_shadow_summary.csv
  phase650_shadow_trades.csv
```

## Verdict

`phase650_pbv2_flat_band_shadow_done`
