# Phase396 — Runtime Position-CAP Mode

Generated: 2026-06-15

## Summary

Runtime CAP is now aligned with capital simulation (Phase267–274): **max concurrent open observer positions until structural EXIT**, not 5-minute virtual-hold gate slots.

| Item | Before (Phase395) | After (Phase396) |
|------|-------------------|------------------|
| CAP target | `open_slots` (VH ~300s) | `observer.open_count()` |
| Slot release | `entry_time + virtual_hold_sec` | structural EXIT / session close |
| 6/15 PM accepted (gate stream) | 90 | **22** (matches capital sim) |
| 6/15 PM CAP rejects | 1703 (VH) | **58** (position-CAP) |
| Discord 保有枠 | gate slots | observer open positions |

**Rollback:** set `position_cap_mode: false` in YAML.

---

## Implementation

### 1. Config (`configs/...trailing_mfe_shadow.yaml`)

```yaml
position_cap_mode: true
position_cap_release: structural_exit
virtual_hold_sec: 300
entry_cooldown_sec: 300
```

- `virtual_hold_sec` / `entry_cooldown_sec` remain for candidate metadata only — **not** CAP occupancy.
- Loaded in `src/small_paper/config.py` → `ExposureGateConfig.position_cap_mode`.

### 2. Exposure Gate (`src/research/exposure_gate.py`)

- `evaluate_entry(..., observer_open_count=, observer_symbol_open=)`
- When `position_cap_mode=true`: reject `REJECT_MAX_CONCURRENT` if `observer_open_count >= max` (unless same-symbol overlap replace).
- `record_accepted` does **not** append `open_slots` in position-CAP mode.

### 3. Pilot runner (`src/small_paper/pilot_runner.py`)

- `_evaluate_gate_entry()` passes observer count to gate.
- `_active_cap_count()` for Discord / position rows.
- Legacy VH shadow via `LegacyVirtualHoldShadow` (`src/small_paper/position_cap_mode.py`).
- Summary fields via `position_cap_summary_fields()`.
- Artifacts: `results/reports/phase396_position_cap_runtime_summary.json`, `phase396_legacy_virtual_hold_shadow_events.csv`.

### 4. Discord

- ENTRY detail: `Gate model: position_cap_until_exit`, observer structural note.
- EXIT detail: `Exit source: structural_observer`, session-close slot release note.
- Daily summary: `position_cap_max_open`, `observer_open_max_positions`, `gate_virtual_hold_max_slots`, `session_close_exit_burst_count`.

### 5. `small_paper_summary.json` fields

| Field | Meaning |
|-------|---------|
| `position_cap_mode` | `true` when active |
| `position_cap_release` | `structural_exit` |
| `position_cap_max_open` | Peak observer opens under CAP |
| `observer_open_max_positions` | Peak structural observer open count |
| `gate_virtual_hold_max_slots` | Legacy VH shadow peak (comparison) |
| `accepted_count_position_cap` | Runtime accepted count |
| `rejected_by_position_cap` | CAP rejects (observer count) |
| `legacy_virtual_hold_accepted_count_shadow` | What VH CAP would have accepted |
| `legacy_virtual_hold_delta_accept_count` | Shadow minus runtime accepted |
| `session_close_exit_burst_count` | `observer_exit` with `session_close=true` |

---

## Phase395 vs Phase396 (6/15 PM `live_session_122531`)

Validation: `python scripts/run_phase396_position_cap_validation.py`

| Metric | Phase395 VH Runtime | Phase396 Position-CAP | Capital sim |
|--------|----------------------|------------------------|-------------|
| Accepted | 90 | **22** | **22** |
| CAP rejects | 1703 | **58** | **58** |
| PnL (100株) | ¥46,804 | ¥18,700* | ¥18,700 |
| Max active (CAP layer) | 3 slots | 3 positions | 3 |
| Observer max open | 16 | ≤16 | — |
| 15:23 EXIT burst | 12 (legacy) | ≤3 at CAP† | — |

\*PnL from Phase395 position-CAP replay on `structural_trades.csv`.  
†Under position-CAP, at most 3 positions open at force_close; legacy session had 12 observer exits because VH slots had already freed.

---

## Tomorrow's paper — what to watch

1. **`accepted_count` ≈ 20–25/session** (not ~90) — confirms position-CAP is active.
2. **`rejected_by_position_cap`** — primary CAP pressure metric (not `max_concurrent` VH rejects).
3. **`legacy_virtual_hold_delta_accept_count`** — quantifies VH vs position-CAP gap.
4. **`position_cap_max_open` ≤ 3** — CAP integrity.
5. **`session_close_exit_burst_count` ≤ max_concurrent_positions** — no surprise 12-exit bursts at CAP.
6. **Discord 保有枠** — matches live observer open count.
7. **Phase273/274 forward shadows** — should converge with live accepted stream over time.

---

## Rollback

```yaml
position_cap_mode: false
```

Restores virtual-hold slot CAP (Phase395 behavior). No code deploy required beyond config change.

---

## Tests

```bash
python -m pytest tests/test_position_cap_mode.py -q
python scripts/run_phase396_position_cap_validation.py
```

---

## Files changed

| Path | Change |
|------|--------|
| `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml` | `position_cap_mode: true` |
| `src/small_paper/config.py` | Config fields |
| `src/research/exposure_gate.py` | Position-CAP gate logic |
| `src/small_paper/position_cap_mode.py` | Shadow + summary helpers |
| `src/small_paper/pilot_runner.py` | Runtime wiring |
| `src/small_paper/discord_message_builder.py` | Summary + ENTRY labels |
| `src/small_paper/discord_notifier.py` | EXIT labels |
| `tests/test_position_cap_mode.py` | Unit tests |
| `scripts/run_phase396_position_cap_validation.py` | 6/15 validation |

**Not changed:** Entry conditions, Exit conditions, Universe, order processing, `order_enabled=false`, `paper_only=true`.
