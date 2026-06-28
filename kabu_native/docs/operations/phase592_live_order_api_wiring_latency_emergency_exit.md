# Phase592 — Live Order API Wiring + Latency / Emergency Exit

**Verdict:** `phase592_live_order_api_wiring_latency_emergency_exit_done`

## Scope

- No real orders (`order_enabled=false`, `live_trading_enabled=false`)
- API read probes: wallet, margin, positions, orders
- Payload builder + latency to `would_send`
- STOP EXIT emergency flow dry-run

## Modules

- `src/api/order_read_client.py`
- `src/small_paper/live_order_api_wiring.py`

## Mandatory answers

1. 0.034
2. 0.037
3. 0.032
4. True
5. 0.034
6. 0.032
7. Symbol, Exchange, SecurityType, Side, CashMargin, MarginTradeType, DelivType, AccountType, Qty, FrontOrderType, Price, ExpireDay; EXIT adds ClosePositions or ClosePositionOrder
8. MarginTradeType=3 general credit daytrade
9. True
10. True
11. Use MarginAccountWallet from API; required_margin=price*100/2 as guard
12. order 0.5-1.0s, fill 0.5s, hold 3-5s, reconcile 30-60s
13. cancel + release CAP slot; block new ENTRY if cancel fails
14. never give up — resend market repay until flat; SAFE_STOP only on inquiry fail
15. SAFE_STOP + block ENTRY + Discord emergency alert
16. position mismatch, cancel fail, inquiry fail on STOP EXIT, duplicate unknown order
17. False
18. Phase592 design-only; need phase593 capped live pilot CAP=2
19. phase593_live_order_capped_pilot_cap2
20. False
21. False

## Outputs

- `results/reports/phase592_*.csv`
- `results/reports/phase592_report.json`