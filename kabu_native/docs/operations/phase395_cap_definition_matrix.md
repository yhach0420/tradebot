# Phase395 — CAP Definition Matrix

Generated: 2026-06-15T21:47:44+09:00

## Purpose

Document the three independent CAP definitions so Runtime notifications, observer lifecycle,
capital simulation (Phase267–274), and future live trading share a single mental model.

## Matrix

| Layer | CAP Meaning | Release Condition | Used For | Current Risk |
| --- | --- | --- | --- | --- |
| A. Exposure Gate CAP | max_concurrent_positions=3 limits concurrent entry slots (open_slots), not observer positions | Slot pruned when next evaluate_entry sees exit_time < candidate entry_time (~300s virtual hold from entry) | Runtime entry accept/reject; small_paper_events position_slot_before/after; small_paper_positions.csv; peak_open_slots in summary | Discord ENTRY implies position lifecycle; slots free in ~5min while observer may hold much longer |
| B. Observer Position | No hard concurrent cap — one open virtual position per symbol until structural exit | structural exit (stop_hit, trailing_mfe_exit, overlap_replaced), or close_all at session force_close | structural_trades.csv; structural_events.csv; observer_exit Discord notifications; structural_observer_review.json | Many simultaneous observer_exit at session close; EXIT count ≠ gate slot count |
| C. Capital Simulation (Phase267–274) | max_concurrent_positions on open_positions dict — capital + leverage constrained | process_exit at structural_trades exit_time (close_time); maintenance/equity-floor force-close | phase267_equity_curve*.csv; phase268 reconciliation; phase269 grid; phase272 recommendations; phase273/274 forward shadows | Operators may assume Runtime CAP=3 matches sim; sim holds until structural EXIT, not 5min VH |

## Key Code References

| Layer | Primary module |
|-------|----------------|
| Exposure Gate | `src/research/exposure_gate.py`, `src/small_paper/pilot_runner.py` (`virtual_hold_sec=300`) |
| Observer | `src/small_paper/observer_position_tracker.py` |
| Capital Sim | `src/research/phase385_cap_sensitivity_study.py` (`CapScenarioState`) |

## Terminology

| Term | Gate | Observer | Capital Sim |
|------|------|----------|-------------|
| `max_concurrent_positions` | Entry slot limit (3) | N/A | Open position limit |
| `open_slots` | `(entry_ts, exit_ts, symbol)` | N/A | N/A |
| `open_positions` | N/A | Per-symbol map | Capital occupancy dict |
| Release trigger | `exit_time` prune (~5min) | Structural exit / `close_all` | `structural_trades` exit event |

## Conclusion

**CAP=3 means different things per layer.** Runtime gate CAP is a **5-minute virtual-hold slot**.
Capital simulation CAP is **max 3 positions until structural EXIT**. Observer has **no concurrent cap**.
