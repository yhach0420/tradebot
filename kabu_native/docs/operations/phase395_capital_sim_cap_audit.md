# Phase395 — Capital Simulation CAP Audit (Phase267–274)

Generated: 2026-06-15T21:47:44+09:00

## Verdict: **PASS**

CAP=3 means max 3 open positions until EXIT (structural exit_time from structural_trades.csv)

Capital simulation **does not** use 5-minute virtual hold (`uses_virtual_hold=False`).
Slots release on `process_exit` at `structural_trades.csv` `exit_time` / `close_time`.

---

## Configuration Under Test

| Parameter | Value |
|-----------|-------|
| Initial equity | ¥1,500,000 |
| Leverage | 2.0x |
| Shares | 100 fixed |
| CAP | 3 |
| Stop policy | fixed_stop_1p2 |

---

## Phase Module Summary

| Phase | Module | Default CAP | Release | Buying Power | Leverage / Maint | Input Trades |
| --- | --- | --- | --- | --- | --- | --- |
| 267 | equity_curve_shadow.py | 2 | CapScenarioState.process_exit @ structural exit_time | Yes — compute_buying_power + compute_requested_shares | Yes — MAINT_WARNING / MAINT_STOP_ENTRY / MAINT_FORCE_EXIT | structural_trades.csv (real exit times) |
| 268 | capital_simulation_reconciliation.py | 2 | Same CapScenarioState timeline | Yes | Yes | structural_trades.csv |
| 269 | phase269_portfolio_configuration_optimization.py | Grid 1–5 | Structural exit events | Yes | Yes | structural_trades.csv |
| 272 | phase272_apply_leverage_robustness_to_equity_bucket_recommendation.py | Recommends cap3 @ 1.5M lev2 | Via Phase269 sim engine | Yes | Yes | structural_trades.csv |
| 273 | phase273_live_config_forward_shadow_logger.py | 3 @ 1.5M fixed_stop_1p2 | simulate_audited → process_exit @ structural exit | Yes | Yes | Accumulated structural_trades.csv |
| 274 | phase274_live_config_auto_transition_shadow.py | 3 below 2M equity; 5 at/above 2M | Structural exit; cap band changes on new entries only | Yes | Yes | Accumulated structural_trades.csv |

---

## Simulation Results (full period)

### `simulate_cap` (Phase385 engine)

| Metric | Value |
|--------|-------|
| Accepted trades | 741 |
| Rejected trades | 943 |
| Rejected by CAP (`max_concurrent_positions`) | 892 |
| Rejected by buying power | 51 |
| Max concurrent positions observed | 3 |
| Total PnL (100 shares) | ¥144470.0 |
| Final equity | ¥1644470.0 |

### `simulate_audited` (Phase271/273 engine, fixed_stop_1p2)

| Metric | Value |
|--------|-------|
| Accepted trades | 741 |
| Rejected trades | 943 |
| Reject reason counts | `{"max_concurrent_positions": 892, "insufficient_buying_power": 51}` |
| Max concurrent positions observed | None |
| Total PnL (100 shares) | ¥144470.0 |
| Final equity | ¥1644470.0 |

---

## Evidence: EXIT-until-release (not virtual hold)

`CapScenarioState.try_entry` rejects when `len(open_positions) >= max_concurrent_positions`.
`CapScenarioState.process_exit` removes the key at structural `exit_time`.

**VH vs structural mismatch events** (VH would release slot but sim still holds): **2706**

This proves the sim holds positions until structural EXIT, not 5-minute VH.

### Sample mismatches (first 5)

| key | candidate_entry | vh_would_release_at | structural_exit | still_open_in_sim |
| --- | --- | --- | --- | --- |
| 3687.T|2026-05-29T09:23:19+09:00 | 2026-05-29T09:28:25+09:00 | 2026-05-29T09:28:19+09:00 | 2026-05-29T09:31:40+09:00 | True |
| 2586.T|2026-05-29T09:26:38+09:00 | 2026-05-29T09:31:40+09:00 | 2026-05-29T09:31:38+09:00 | 2026-05-29T09:32:50+09:00 | True |
| 3907.T|2026-05-29T09:33:28+09:00 | 2026-05-29T09:38:31+09:00 | 2026-05-29T09:38:28+09:00 | 2026-05-29T09:39:40+09:00 | True |
| 3103.T|2026-05-29T09:46:46+09:00 | 2026-05-29T09:51:47+09:00 | 2026-05-29T09:51:46+09:00 | 2026-05-29T10:21:18+09:00 | True |
| 3103.T|2026-05-29T09:46:46+09:00 | 2026-05-29T09:53:04+09:00 | 2026-05-29T09:51:46+09:00 | 2026-05-29T10:21:18+09:00 | True |

### Exit release samples (first 5)

| key | exit_time | open_before | release_reason |
| --- | --- | --- | --- |
| 6085.T|2026-05-29T09:13:00+09:00 | 2026-05-29T09:13:06+09:00 | 2 | structural_exit_event |
| 6085.T|2026-05-29T09:13:06+09:00 | 2026-05-29T09:13:10+09:00 | 2 | structural_exit_event |
| 6085.T|2026-05-29T09:13:10+09:00 | 2026-05-29T09:13:23+09:00 | 1 | structural_exit_event |
| 6659.T|2026-05-29T09:18:09+09:00 | 2026-05-29T09:18:18+09:00 | 2 | structural_exit_event |
| 6659.T|2026-05-29T09:18:18+09:00 | 2026-05-29T09:18:22+09:00 | 2 | structural_exit_event |

---

## Buying Power & Leverage

Both engines call `compute_buying_power(equity, gross, leverage_limit)` and `compute_requested_shares`.
Maintenance ratio checks (`MAINT_WARNING`, `MAINT_STOP_ENTRY`, `MAINT_FORCE_EXIT`) are active in `CapScenarioState.try_entry`.

---

## FAIL Criteria Check

| Check | Result |
|-------|--------|
| CAP=3 means virtual slot or 5min hold | **No** — sim uses structural exit |
| CAP=3 means max 3 open until EXIT | **Yes** |
| Buying power enforced | **Yes** (`insufficient_buying_power_count` tracked) |
| Leverage / maintenance enforced | **Yes** |
