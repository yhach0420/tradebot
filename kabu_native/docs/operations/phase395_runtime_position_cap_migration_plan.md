# Phase395 — Runtime Position-CAP Migration Plan (proposal)

Generated: 2026-06-15T21:47:46+09:00

**Not implemented in Phase395.** For a future phase after shadow validation.

---

## Goal

Align Runtime CAP, Discord notifications, observer lifecycle, and capital simulation
(1.5M / lev2 / 100 / CAP3 / fixed_stop_1p2) so ENTRY/EXIT reflect actual tradable positions.

---

## Target: Runtime Position-CAP Mode

| Component | Current | Target |
|-----------|---------|--------|
| `max_concurrent_positions` | Gate virtual-hold slots | Observer open position count |
| Slot release | `entry_time + 300s` | Structural EXIT (`stop_hit`, `trailing_mfe_exit`, overlap, session close) |
| 5-minute VH | Concurrent cap occupancy | Rename to `entry_cooldown_sec` (optional, separate concern) |
| Discord ENTRY | Gate accept | Same trigger, label as position lifecycle |
| Discord EXIT | Observer structural | Matches gate slot release |
| Capital sim input | `structural_trades.csv` | Unchanged — already position-CAP |

---

## Migration Phases

### Phase A — Shadow validation (Phase395, done)

- Position-CAP shadow parallel to production
- 6/15 PM comparison report
- No Runtime changes

### Phase B — Label / audit only

- Discord semantics proposal (this phase)
- Optional audit fields in `small_paper_summary.json` (`gate_max_active_positions`, `observer_open_max_positions`)

### Phase C — Runtime switch (future)

1. Move CAP check from `ExposureGate.evaluate_entry` slot prune to `ObserverPositionTracker.open_count()`
2. Reject `REJECT_MAX_CONCURRENT` when `observer.open_count() >= max_concurrent_positions`
3. Release gate `open_slots` on observer exit callback (or deprecate `open_slots`)
4. Retire VH-as-CAP; keep VH only if needed as cooldown metadata
5. Update `small_paper_positions.csv` to track position-cap slots

### Phase D — Verification

- Re-run Phase395 comparison: virtual-hold vs position-cap delta should → 0
- Capital sim forward shadow (Phase273/274) vs live session PnL convergence test
- Discord EXIT count at session close should match open position count

---

## Risks

| Risk | Mitigation |
|------|------------|
| Lower accepted rate (position CAP stricter than VH) | Phase395 shadow quantifies delta (20260615 PM) |
| Entry timing shift | Shadow replay before flip |
| Operator confusion during transition | Dual-label period (gate + position metrics) |

---

## Rollback

Keep `virtual_hold_sec` config; feature flag `position_cap_mode: false` restores gate VH behavior.

---

## Success Criteria

1. `peak_open_slots` ≈ `observer_open_max` ≈ capital sim `max_concurrent_positions_observed`
2. Discord EXIT count at session end ≤ CAP (no burst >> CAP without explicit session-close label)
3. Forward shadow equity tracks live within documented tolerance
