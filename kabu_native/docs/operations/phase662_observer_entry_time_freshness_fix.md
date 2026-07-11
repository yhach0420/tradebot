# Phase662 — Observer Entry Time Freshness Fix

**Verdict:** `phase662_observer_entry_time_freshness_fix_done`

## Problem
Stale `CurrentPriceTime` propagated to observer `entry_time`, causing
`no_progress_exit` to fire immediately after a fresh ENTRY notification.

## Fix
- Observer hold clock uses `accepted_at` / `accepted_event_time` (accept time).
- Market timestamps stored separately (`market_entry_time`, `current_price_time`).
- `position_id` includes microsecond observer entry stamp.
- Discord EXIT shows observer-based hold minutes + `market_time_age_sec` + `stale_trade`.

## 6327.T reproduction
- accept: `2026-07-07T12:58:53+09:00`
- market: `2026-07-07T12:44:14+09:00`
- hold at +30s: 30.0s (no_progress=False)
- Discord hold display: 0 min

## Artifacts
- `results/reports/phase662_observer_entry_time_freshness_fix/phase662_report.json`
- `results/reports/phase662_observer_entry_time_freshness_fix/phase662_6327_repro.csv`
- `results/reports/phase662_observer_entry_time_freshness_fix/phase662_position_id_regression.csv`
