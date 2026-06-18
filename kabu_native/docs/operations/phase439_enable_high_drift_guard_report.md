# Phase439 — Enable High Drift Pullback Guard Runtime

Generated: 2026-06-18

## Summary

Phase439 enables **High Drift Pullback Guard** as a production ENTRY reject guard on Dynamic40,
replacing the legacy VWAP pullback guard (Phase355) which removed **0 trades** in 20260529–20260618.

## Runtime changes

| Item | Status |
|------|--------|
| High Drift guard wired into `ExposureGate` | Done |
| `reject_reason` = `high_drift_pullback` | Done |
| `small_paper_rejects.csv` / session summary counts | Done |
| Discord summary count (no per-reject notify) | Done |
| Legacy VWAP pullback disabled in production YAML | Done |

## Guard condition (unchanged from Phase436)

```
dynamic40 AND (
  (day_high >= 1.2% AND r10 < -0.15% AND r5 > r10)
  OR
  (day_high >= 1.5% AND (r15 < -0.5% OR r5 < -0.5%))
)
```

Fields at ENTRY:
- `entry_rise_5min_pct`, `entry_rise_10min_pct`, `entry_rise_15min_pct` (new 15m via price ring)
- `day_high_distance_pct` / `entry_near_day_high_pct`

## YAML (preflight)

File: `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

```yaml
high_drift_guard_enabled: true
legacy_vwap_pullback_guard_enabled: false
enable_pullback_misread_dynamic40_guard: false
order_enabled: false
paper_only: true
max_concurrent_positions: 5
same_symbol_open_policy: no_overlap_replace
```

## Rollback

Set in YAML:

```yaml
high_drift_guard_enabled: false
legacy_vwap_pullback_guard_enabled: true
# or: enable_pullback_misread_dynamic40_guard: true
```

No code deploy required beyond config change.

## Tomorrow verification checklist

1. Session `small_paper_summary.json`: `high_drift_pullback_guard_enabled=true`, `high_drift_pullback_reject_count` > 0 on drift days
2. `small_paper_rejects.csv`: rows with `reject_reason=high_drift_pullback`
3. Discord AM/PM summary line: `HighDriftPullback Guard: reject=N`
4. `pullback_misread_dynamic40_reject_count` stays **0** (VWAP guard off)
5. Accepted trades still pass `Momentum:low` + `Board:mid` gate (entry score unchanged)
6. No `order_enabled` / live orders
7. Spot-check 6976-style entries (day_high≥1.2%, negative r10, small bounce) are rejected

## Code touchpoints

- `src/small_paper/high_drift_pullback_entry_guard.py` — guard logic + state
- `src/research/exposure_gate.py` — ENTRY reject integration
- `src/small_paper/config.py` — YAML flags
- `src/small_paper/pilot_runner.py` — enrichment, rejects, summary
- `src/small_paper/extended_entry_shadow.py` — `entry_rise_15min_pct`
- `tests/test_phase439_high_drift_guard_runtime.py`

## Mandatory answers

1. **Runtime反映完了**: Yes — High Drift guard active via `ExposureGate` when `high_drift_guard_enabled=true`
2. **VWAP guard無効化完了**: Yes — `legacy_vwap_pullback_guard_enabled=false` in production YAML
3. **rollback方法**: `high_drift_guard_enabled: false` (+ optionally re-enable legacy VWAP)
4. **明日の確認項目**: See checklist above
