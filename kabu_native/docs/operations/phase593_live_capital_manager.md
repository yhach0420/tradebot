# Phase593 — Live Capital Manager

**Verdict:** `phase593_live_capital_manager_done`

## Executive summary

- `LiveCapitalManager` implemented in `src/small_paper/live_capital_manager.py`.
- Runtime hook logs capital checks to `live_capital_check.jsonl` (does **not** block paper ENTRY).
- Uses kabusapi `StockAccountWallet`, `MarginAccountWallet`, positions, orders.
- CAP and margin/buying_power checks are **separate** with distinct reject reasons.
- Pending buy orders consume CAP slots; duplicate symbols blocked.

## Live account snapshot

- stock_wallet: 20000.0
- margin_wallet: 0.0
- can_enter (live): False
- reject_reason: insufficient_margin_or_buying_power

## Mandatory answers

1. True
2. True
3. entry_price * 100 / leverage_limit (2.0)
4. True
5. True
6. True
7. False
8. insufficient_margin_or_buying_power
9. {'cap': 2, 'entry_price': 2768.0, 'required_margin_per_slot': 138400.0, 'total_required_margin': 276800.0, 'min_equity_for_buying_power': 138400.0, 'min_margin_wallet': 276800.0}
10. {'cap': 5, 'entry_price': 2768.0, 'required_margin_per_slot': 138400.0, 'total_required_margin': 692000.0, 'min_equity_for_buying_power': 346000.0, 'min_margin_wallet': 692000.0}
11. True
12. buying_power=max(0,equity*2-gross); cap before margin; same required_margin formula as Phase592B
13. False
14. order_enabled=false; live_trading_enabled=false; dry_run logging only
15. phase593_live_order_capped_pilot_cap2

## Outputs

- `phase593_live_capital_check.csv`
- `phase593_live_capital_rejects.csv`
- `phase593_report.json`