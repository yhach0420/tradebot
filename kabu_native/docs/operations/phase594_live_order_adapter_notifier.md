# Phase594 — LiveOrderAdapter + LiveOrderNotifier

**Verdict:** `phase594_live_order_adapter_notifier_done`

## Architecture

1. **LiveCapitalManager** — capital/CAP checks only
2. **LiveOrderAdapter** — payload + dry-run state machine
3. **LiveOrderNotifier** — JSONL/Discord visibility

## Mandatory answers

1. True
2. True
3. True
4. True
5. True
6. True
7. True
8. True
9. True
10. True
11. True
12. True
13. True
14. ['Fund margin wallet (Phase592A)', 'CAP=2 live pilot with order_enabled=true', 'Real sendorder implementation', 'Position reconcile + emergency exit automation']
15. phase595_live_order_send_pilot_cap2

## Outputs

- `phase594_live_order_notifier_events.csv`
- `phase594_live_order_adapter_dryrun.csv`
- `phase594_live_order_visibility_checks.csv`
- `phase594_report.json`