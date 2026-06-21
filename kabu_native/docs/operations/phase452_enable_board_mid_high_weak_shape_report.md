# Phase452 — Enable Board Mid+High and Weak Shape Reject

Generated: 2026-06-19
Status: **Runtime ENTRY updated**

## Summary

Runtime ENTRY gate updated from:

- **Before:** `Momentum:low + Board:mid + High Drift`
- **After:** `Momentum:low + (Board:mid OR Board:high) + High Drift + Weak Shape Reject`

Scope: **ENTRY gate only.** Exit, Order, No Progress, High Drift conditions, Discord per-trade format, CAP5, `no_overlap_replace`, and intraday refresh are unchanged.

## Part A — Board:high ENTRY permit

**File:** `src/small_paper/entry_expectancy_score_shadow.py`

| Token | Score |
|-------|-------|
| Momentum:low | 2 |
| Board:mid | 1 |
| Board:high | 1 |
| **Required total** | **3** |

Gate logic (`exposure_gate.py`):

1. `momentum_low_required_for_v2`
2. `board_mid_or_high_required_for_v2` — Board:low still rejected
3. `entry_expectancy_score_v2 >= 3`

`active_score_tokens_v2` continues to log `Board:high` when the board tertile is high.

## Part B — Weak Shape Reject guard

**File:** `src/small_paper/weak_shape_reject_entry_guard.py`

**Reject reason:** `weak_shape_reject`

**Blocked shapes (intraday, forward-safe):**

| Shape | Condition |
|-------|-----------|
| `opening_peak` | Day high within 20 min of open AND pullback from high ≥ 1.5% |
| `slow_opening_peak` | Day high within 60 min of open AND pullback from high ≥ 2.0% |

**Uptrend pass (no reject):** recent high update ≤ 15 min, or positive r15 with r10/r30, or mins-from-open > 60 with r15 > 0.

**Features used (ENTRY-time only):**

- `day_high_minutes_from_open`
- `minutes_since_day_high_update`
- `day_high_distance_pct` / `high_to_now_drawdown_pct`
- `entry_rise_5/10/15/30min_pct`
- Price ring + board high via `compute_day_high_timing_fields`

**Not used:** EOD close, `eod_shape_class`, session close price.

### Phase451B E vs Runtime delta

| Aspect | Phase451B `E_weak_shape_reject` | Phase452 Runtime |
|--------|-----------------------------------|------------------|
| Shape source | EOD `eod_shape_class` (lookahead) | Intraday ticks + board high |
| Uptrend handling | EOD label `uptrend` passes | Forward-safe momentum / high-update rules |
| OP/SOP detection | Full-day shape after close | Approximation at ENTRY from timing + pullback |

Runtime intentionally uses a **forward-safe approximation** of Phase445/451B intent. Expect some mismatch vs backtest E on borderline shapes; monitor `weak_shape_class` in rejects.

**YAML:**

```yaml
weak_shape_reject_enabled: true
```

**Rollback:**

```yaml
weak_shape_reject_enabled: false
```

Board:high permit is always active once deployed (no separate flag). To revert to mid-only board, restore pre-Phase452 `SCORE_POINTS_V2` / gate logic.

## Part C — Logging

| Artifact | Fields |
|----------|--------|
| `small_paper_rejects.csv` | `reject_reason=weak_shape_reject`, weak-shape diagnostic columns |
| `small_paper_summary.json` | `weak_shape_reject_count`, `board_mid_entry_count`, `board_high_entry_count` |
| Discord session summary | `WeakShape Reject: count=N`, `BoardHigh ENTRY: count=N` |

Per-trade Discord notification format unchanged.

## Part D — Preflight checklist

Pilot YAML: `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

| Flag | Expected |
|------|----------|
| `paper_only` | `true` |
| `order_enabled` | `false` |
| `max_concurrent_positions` | `5` |
| `same_symbol_open_policy` | `no_overlap_replace` |
| `high_drift_guard_enabled` | `true` |
| `no_progress_exit_enabled` | `true` |
| `weak_shape_reject_enabled` | `true` |
| `legacy_vwap_pullback_guard_enabled` | `false` |

## Part E — Tests

`tests/test_phase452_board_mid_high_weak_shape.py`

Run:

```bash
python -m pytest tests/test_phase452_board_mid_high_weak_shape.py -v
```

## Mandatory answers

1. **Board:high ENTRY permit:** Done — `Board:high` scores +1; mid\|high required for v2 pass.
2. **Weak Shape Reject:** Done — production guard after High Drift, before v2 score cap checks.
3. **No lookahead:** Confirmed — guard module does not reference EOD close or `eod_shape_class`.
4. **YAML:** `weak_shape_reject_enabled: true` in pilot config.
5. **Rollback:** Set `weak_shape_reject_enabled: false` (disables weak-shape reject only).
6. **Tomorrow verification (2026-06-20 session):**
   - Confirm `paper_only=true`, no live orders
   - `small_paper_summary.json`: `weak_shape_reject_count`, `board_high_entry_count`, `board_mid_entry_count`
   - Discord summary lines: WeakShape Reject / BoardHigh ENTRY
   - Sample rejects: `reject_reason=weak_shape_reject`, `weak_shape_class` in (`opening_peak`, `slow_opening_peak`)
   - Board:high entries appear in accepted trades with `active_score_tokens_v2` containing `Board:high`
   - High Drift / No Progress / CAP5 behavior unchanged vs prior session
   - Compare reject volume to Phase451B E expectation (research used EOD labels; runtime may differ slightly)

## Files changed

- `src/small_paper/entry_expectancy_score_shadow.py`
- `src/small_paper/weak_shape_reject_entry_guard.py` (new)
- `src/research/exposure_gate.py`
- `src/small_paper/config.py`
- `src/small_paper/extended_entry_shadow.py`
- `src/small_paper/pilot_runner.py`
- `src/small_paper/discord_message_builder.py`
- `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`
- `tests/test_phase452_board_mid_high_weak_shape.py`
- `docs/operations/phase452_enable_board_mid_high_weak_shape_report.md`
