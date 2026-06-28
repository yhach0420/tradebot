# Phase592B — Equity Simulation Capital Logic Audit

**Verdict:** `phase592b_equity_sim_capital_logic_audit_done`

## Executive summary

- **initial_capital** is a **fixed anchor** (`1,500,000` yen); it is never reduced by losses.
- **current_equity** is **variable**: `initial_capital + realized_pnl`, updated on each simulated exit.
- **buying_power / available_margin** = `current_equity × 2.0 − gross_position_value`.
- **required_margin** per 100-share slot = `entry_price × 100 / 2.0`.
- **CAP=5**: reject when `open_positions >= 5` (`max_concurrent_positions`).
- CAP skips and margin skips use **separate counters** (`position_cap_reject_count` vs `insufficient_buying_power_count`).

## Simulation results (CAP=5, canonical trades)

- accepted: 1067
- rejected: 149
- cap rejects: 80
- margin rejects: 69
- final equity: 1676447.98 (PnL 176447.98)

## Mandatory answers

1. initial_capital fixed anchor; current_equity = initial_capital + realized_pnl (variable)
2. True
3. required_margin_per_slot = entry_price * shares / leverage_limit (100-share slot: entry_price * 100 / 2.0)
4. buying_power = max(0, current_equity * leverage_limit - gross_position_value); leverage_limit=2.0 in FIXED_SPEC
5. len(open_positions) >= 5 → reject reason max_concurrent_positions (position_cap_reject_count=80)
6. True
7. 69
8. True
9. CapScenarioState._reject_entry increments position_cap_reject_count vs insufficient_buying_power_count separately
10. Partially — no CapitalManager module exists; CapScenarioState logic maps to live preflight but needs kabu wallet/margin sync (Phase592A)
11. False
12. Research sim correctly uses variable equity with separated skip counters; live wiring should mirror compute_buying_power + cap check order
13. phase593_live_order_capped_pilot_cap2
14. ['research.phase385_cap_sensitivity_study.CapScenarioState', 'research.phase383_realistic_credit_sizing_backtest.compute_buying_power', 'research.equity_curve_shadow.EquityCurveCapState']
15. {'initial_capital': 1500000.0, 'final_equity': 1676447.98, 'realized_pnl': 176447.98, 'leverage': 2.0, 'shares': 100, 'cap': 5, 'equity_floor': 750000.0}
16. {'position_cap_reject_count': 80, 'insufficient_buying_power_count': 69, 'maintenance_stop_count': 0, 'accepted_trade_count': 1067, 'rejected_trade_count': 149}

## Canonical code paths

- `CapScenarioState.current_equity()` → `initial_equity + realized_pnl`
- `compute_buying_power()` → `equity * leverage - gross`
- `CapScenarioState.try_entry()` → cap check then `compute_requested_shares()`
- `CapScenarioState._reject_entry()` → separate cap vs margin counters

## Outputs

- `phase592b_equity_sim_logic_audit.csv`
- `phase592b_equity_curve_margin.csv`
- `phase592b_skip_reason_breakdown.csv`
- `phase592b_report.json`