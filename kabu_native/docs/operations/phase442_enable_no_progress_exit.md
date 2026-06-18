# Phase442 — Enable No Progress Exit Runtime

Generated: 2026-06-18

## Summary

Phase427 / Phase429A / Phase441 validated **No Progress Exit** (`linmfe_t900_i0p6_s0p05_c0p8_p0p3`) as the preferred stagnation exit. Boundary Exit is a superset but its boundary-only bucket was net negative; **Boundary is not adopted**.

Phase442 enables **No Progress Exit only** on the production structural EXIT path (`combined_structural_exit_v1_trailing_mfe_shadow`).

## Policy

| Parameter | Value |
|-----------|-------|
| policy_key | `linmfe_t900_i0p6_s0p05_c0p8_p0p3` |
| start | 900 sec |
| required MFE | 0.6 + 0.05 per 5 min (cap 0.8) |
| current pnl | < 0.3% |
| exit_reason | `no_progress_exit` |

Priority on each tick (unchanged except NP insert): **hard stop → no_progress_exit → board-dynamic trailing MFE**.

## Runtime changes

| Item | Status |
|------|--------|
| `no_progress_exit.py` runtime policy | Done |
| Wired into `simulate_structural_policy` / observer | Done |
| `exit_reason=no_progress_exit` on fire | Done |
| `no_progress_exit_count` in summary / Discord shadow lines | Done |
| `no_progress_exit` column on events / structural trades | Done |
| YAML `no_progress_exit_enabled: true` | Done |

## YAML (preflight)

File: `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

```yaml
no_progress_exit_enabled: true
structural_exit_policy: combined_structural_exit_v1_trailing_mfe_shadow
```

## Rollback

Set in YAML (no code deploy required):

```yaml
no_progress_exit_enabled: false
```

## Tomorrow verification checklist

1. `small_paper_summary.json`: `no_progress_exit_enabled=true`, `no_progress_exit_count` ≥ 0 on long-hold stagnation days
2. `small_paper_events.csv`: rows with `exit_reason=no_progress_exit`, `no_progress_exit=true`
3. `structural_trades.csv`: `exit_reason=no_progress_exit` where applicable
4. Discord AM/PM summary shadow line: `NoProgress Exit: count=N`
5. Hard stop / trailing MFE / overlap / session_close behaviour unchanged
6. High Drift guard still active (`high_drift_guard_enabled=true`)
7. `order_enabled: false` / paper-only unchanged

## Code touchpoints

- `src/small_paper/no_progress_exit.py` — policy logic
- `src/research/structural_exit_policies.py` — EXIT integration
- `src/small_paper/observer_position_tracker.py` — live tick evaluation
- `src/small_paper/config.py` — YAML flag
- `src/small_paper/pilot_runner.py` — event fields + summary count
- `src/small_paper/discord_message_builder.py` — summary count line
- `tests/test_phase442_no_progress_exit.py`

## Mandatory answers

1. **Runtime反映完了**: Yes — No Progress Exit active when `no_progress_exit_enabled=true`
2. **Exit理由追加完了**: Yes — `no_progress_exit` in official structural reasons + events
3. **YAML反映完了**: Yes — production preflight YAML updated
4. **rollback方法**: `no_progress_exit_enabled: false` in YAML
5. **明日の確認項目**: See checklist above
