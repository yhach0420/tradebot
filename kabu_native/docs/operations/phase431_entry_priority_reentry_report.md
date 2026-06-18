# Phase431 — Entry Priority & Immediate Reentry Audit

Generated: 2026-06-17T23:06:06+09:00
Target: 20260617
Verdict: **reentry_positive**

## Part A — Immediate Reentry

| window | count | PF | total PnL | avg PnL | win_rate |
|--------|-------|-----|-----------|---------|----------|
| ≤30s | 13 | 4.6803 | 18401.05 | 1415.47 | 0.7692 |
| ≤60s | 13 | 4.6803 | 18401.05 | 1415.47 | 0.7692 |
| ≤180s | 15 | 5.0803 | 20401.05 | 1360.07 | 0.8 |
| ≤300s | 18 | 2.4573 | 15301.07 | 850.06 | 0.7222 |

Non-reentry trades: count=22 PF=0.5368 total=-21999.65 avg=-999.98

## Part B — Entry Priority

- within-scan order: **rank_score descending (not raw PUSH order)**
- CAP-full order: **PUSH processing order (first gate-pass wins)**
- candidate queue: **True** (EntryScanController 2s scan batch + rank_score)
- max_concurrent rejects: **1221**
- high score (v2≥5) CAP rejects: **0**
- max_entries_per_scan rejects: **231**

## Part C — Reentry × Priority Interaction

- correlated cases: **16**
- reentry PnL in those cases: **14401.05** yen

## 必須回答

- 1_reentry_favorable_or_not: favorable
- 2_ban_candidate: False
- 3_priority_logic: scan batch: rank_score; CAP full: PUSH arrival order
- 4_score_or_fifo: within-scan: score rank; CAP: FIFO (PUSH order)
- 5_high_score_miss_examples: 0
- 6_cap5_impact: {'max_concurrent_rejects': 1221, 'reentry_cap_interactions': 16}
- 7_improvement_room: Consider reentry cooloff after EXIT; rank_score tie-break audit; CAP queue by score when slots free

### Part A counts

- ≤30s: count=13 PF=4.6803 PnL=18401.05
- ≤60s: count=13 PF=4.6803 PnL=18401.05
- ≤180s: count=15 PF=5.0803 PnL=20401.05
- ≤300s: count=18 PF=2.4573 PnL=15301.07