# Phase592A — Kabu API Online Verification

**Verdict:** `phase592a_kabu_api_online_verification_done`
**preflight_ready:** `True`

## Mandatory answers

1. True
2. True
3. True
4. True
5. True
6. True
7. {'token': {'avg_ms': 1.736, 'median_ms': 1.736, 'p95_ms': 1.736, 'max_ms': 1.736}, 'wallet': {'avg_ms': 1.58, 'median_ms': 1.632, 'p95_ms': 1.755, 'max_ms': 1.755}, 'wallet_margin': {'avg_ms': 5.033, 'median_ms': 1.675, 'p95_ms': 12.15, 'max_ms': 12.15}, 'positions': {'avg_ms': 4.879, 'median_ms': 1.549, 'p95_ms': 11.935, 'max_ms': 11.935}, 'orders': {'avg_ms': 4.836, 'median_ms': 1.69, 'p95_ms': 11.583, 'max_ms': 11.583}, 'executions': {'avg_ms': 1.474, 'median_ms': 1.59, 'p95_ms': 1.613, 'max_ms': 1.613}}
8. wallet_margin p95=12.15ms
9. MarginTradeType=3 (general credit daytrade) recommended
10. True
11. True
12. True
13. True
14. False
15. MarginAccountWallet=0.0; cash=250000.0; required_per_slot=138400
16. True
17. False
18. Phase592A verification only; Phase593 CAP=2 pilot next

## Outputs

- `phase592a_api_capability_online.csv`
- `phase592a_api_rtt.csv`
- `phase592a_payload_validation.csv`
- `phase592a_margin_capacity.csv`
- `phase592a_stop_exit_payload_validation.csv`
- `phase592a_live_order_preflight.csv`
- `phase592a_report.json`