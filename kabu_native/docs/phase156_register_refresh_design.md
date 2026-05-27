# Phase 156: Kabu register refresh design (review only)

## Policy (candidate 1 — recommended first)

- At 10:00 and 14:30: regenerate universe CSV (Core10 + Dynamic40, price-risk filtered).
- **Do not** force-exit positions dropped from the new universe.
- **New entries only** use the latest universe.
- Session close windows unchanged (AM ~11:25, PM ~15:23).

## Register sequence at each refresh

1. Build target symbol set (priority merge, max 50).
2. `PUT /unregister/all` (full PUSH table — Phase 155).
3. `PUT /register` with merged list (≤50).

## Merge priority (must not drop open symbols)

| Priority | Bucket | Notes |
|----------|--------|-------|
| 1 | `open_symbols` | All currently held; required for exit monitoring |
| 2 | Core10 | Always include after open set |
| 3 | Dynamic fill | Trim from bottom of rank until total ≤ 50 |

If `len(open) + len(core10) > 50`: shrink Dynamic to zero first; if still >50, document alert (`open_position_register_issue`) — should not happen with cap≤5.

## Universe CSV naming

- `universe_core10_dynamic40_price_risk_am_YYYYMMDD.csv`
- `universe_core10_dynamic40_price_risk_am_refresh1000_YYYYMMDD.csv`
- `universe_core10_dynamic40_price_risk_pm_YYYYMMDD.csv`
- `universe_core10_dynamic40_price_risk_pm_refresh1430_YYYYMMDD.csv`

Columns: `refresh_time`, `universe_slot`, `source_bucket`, `is_open_position_carried`, `price_risk_flag`, `tick_ratio_pct`.

## Safety constraints (unchanged for shadow)

- `order_enabled=false`, `paper_only=true`
- Production YAML not modified in Phase 156
- `safety.check_max_concurrent` still caps at 3 until shadow-only relaxation lands

## Daily runner timing (proposed, not wired)

| Slot | Time (JST) | Action |
|------|------------|--------|
| AM initial | 09:00 | universe + register |
| AM refresh | 10:00 | refresh CSV + register merge |
| PM initial | 12:25 | universe + register |
| PM refresh | 14:30 | refresh CSV + register merge |
