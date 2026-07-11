# Phase591 — Live Trading Architecture Design

> **Source of Truth (Phase687W3+):** [`docs/live_trading/live_order_system_design.md`](../live_trading/live_order_system_design.md)  
> Phase591–594 docs remain historical for the earlier dry-run adapter stack.  
> Current SafetySM status: **DRYRUN_MOCK_ONLY** — Runtime wiring `NOT_CONNECTED`; Kabu submit `PRODUCTION_FORBIDDEN`.

**Verdict:** `phase591_live_trading_architecture_design_done`

## Scope

- Paper Runtime ENTRY/EXIT logic **unchanged**
- **No real orders** (`trading_enabled=false`, `live_trading_enabled=false`)
- Dry-run adapter: `src/small_paper/live_order_dry_run_adapter.py`

## Runtime hooks

| Signal | Hook |
|--------|------|
| ENTRY accepted | `_execute_accepted_entry` → `_maybe_record_live_order_entry` |
| EXIT structural | `_log_and_dispatch_observer_events` → `_maybe_record_live_order_exit` |
| Session end | `reconcile_session_positions` → `observer.close_all` |

## Session JSONL outputs

- `live_order_intent.jsonl`
- `live_order_state.jsonl`
- `live_position_reconcile.jsonl`

## Mandatory answers

1. _execute_accepted_entry (ENTRY) and _log_and_dispatch_observer_events/OBSERVER_EXIT (EXIT); adapter module live_order_dry_run_adapter.py
2. 100 shares credit_new limit @ AskPrice; timeout 4s; cancel on no-fill (live phase)
3. market for stop/session_end; limit_aggressive for trailing/profit; partial fill tracked
4. observer CAP + LiveOrderDryRunSession.cap_slots_reserved; pending orders consume slot
5. required_margin = price*100/2; pre-entry wallet check; gross exposure cap
6. yes — ENTRY_ORDER_SENT through cancel/close holds 1 CAP slot
7. track filled_quantity; remain in PARTIAL state; exit retries for remainder
8. poll orders/executions/positions; reconcile_session_positions; mismatch -> SAFE_STOP
9. consecutive failures -> SAFE_STOP + block new entry; cancel fail -> SAFE_STOP
10. one track per symbol; client_order_id dedupe; same_symbol_open_policy
11. observer.close_all -> market exit intents; reconcile all positions zero
12. token, credit, wallet, positions flat, WS, config, trading_enabled=false, dry_run=true
13. CAP=2 recommended first live week; ramp to 5 after 10+ clean sessions
14. src/small_paper/live_order_dry_run_adapter.py (Phase591 dry-run); Phase592 live send
15. phase592_live_order_adapter_kabu_api_wiring

## Outputs

- `results/reports/phase591_paper_runtime_integration_points.csv`
- `results/reports/phase591_order_state_machine.csv`
- `results/reports/phase591_order_policy.csv`
- `results/reports/phase591_risk_capital_rules.csv`
- `results/reports/phase591_position_reconciliation.csv`
- `results/reports/phase591_error_handling_matrix.csv`
- `results/reports/phase591_dry_run_order_adapter.csv`
- `results/reports/phase591_live_trading_preflight_design.csv`
- `results/reports/phase591_report.json`