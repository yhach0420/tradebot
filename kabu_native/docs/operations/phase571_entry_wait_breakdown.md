# Phase571 — Entry Wait Breakdown Analysis

**Verdict:** `phase571_entry_wait_breakdown_done`
**Period:** 20260529-20260626
**Trades:** 3297 (audit=1474, events_fallback=1823)

## Mandatory answers

1. primary wait factor: **universe_wait**
2. board wait avg sec: **1949.0**
3. momentum wait avg sec: **923.1**
4. volume wait avg sec: **633.6**
5. push wait avg sec: **1248.4**
6. processing delay present: **True** (2212 trades >5s)
7. 5471 ~26min wait: **push_wait** — {'entry_time': '2026-06-25T11:14:41+09:00', 'wait_board_sec': 148.0, 'wait_cap_sec': 0.0, 'wait_push_sec': 3968.0, 'wait_universe_sec': 1840.0, 'primary_wait_reason': 'push_wait'}
8. runtime anomaly: **False** — Delays align with gate occupancy; no schedule/PUSH start anomaly
9. board dominant: **True**
10. improvement headroom: **monitor_board_guards**
11. runtime change needed: **False**
12. next phase: **phase572_entry_wait_shadow_monitor**

## Wait category summary

- board_wait: count=1176 (35.7%) avg=3578.6s
- momentum_wait: count=234 (7.1%) avg=2436.4s
- volume_wait: count=50 (1.5%) avg=3367.6s
- cap_wait: count=82 (2.5%) avg=2600.3s
- push_wait: count=338 (10.2%) avg=3456.4s
- universe_wait: count=1411 (42.8%) avg=3635.7s
- processing_wait: count=6 (0.2%) avg=2467.5s

