# Phase437B — High Drift Robustness Walk Forward

Generated: 2026-06-18T22:07:33+09:00
Period: 20260529..20260618
**Verdict:** `6976_dependent`

## Mandatory answers

1. 20260617 improved: **True** (delta 23,400 yen)
2. 20260618 improved: **True** (delta 84,200 yen)
3. LOO mean delta: **7,000 yen**
4. Improved days: **3**
5. Worsened days: **1**
6. Max worsened day: **20260615**
7. 6976 contribution rate: **0.7473**
8. Top3 contribution rate: **0.7317**
9. Overfit judgment: **high_concentration**
10. Runtime shadow recommended: **False**

## Part A — Walk Forward

| case | test_day | baseline PnL | high_drift PnL | delta | delta_pf | delta_stop |
|------|----------|--------------|----------------|-------|----------|------------|
| case1 | 20260617 | 4,000 | 27,400 | 23,400 | 0.8316 | -3 |
| case2 | 20260618 | -98,200 | -14,000 | 84,200 | 0.3623 | -7 |

## Part B — Leave-One-Day-Out (test day delta)

| day | baseline | guard | delta |
|-----|----------|-------|-------|
| 20260529 | -1,621 | -1,621 | 0 |
| 20260601 | -16,161 | -16,161 | 0 |
| 20260602 | -41,350 | -41,350 | 0 |
| 20260603 | 5,401 | 5,401 | 0 |
| 20260608 | 50,200 | 50,200 | 0 |
| 20260609 | 28,999 | 28,999 | 0 |
| 20260610 | 8,401 | 8,401 | 0 |
| 20260611 | 84,699 | 84,699 | 0 |
| 20260612 | 9,700 | 9,700 | 0 |
| 20260615 | 700 | -29,200 | -29,901 |
| 20260616 | 12,800 | 26,100 | 13,300 |
| 20260617 | 4,000 | 27,400 | 23,400 |
| 20260618 | -98,200 | -14,000 | 84,200 |

## Part C — Threshold sensitivity (full period)

| param | value | PnL | PF | delta_pnl | removed |
|-------|-------|-----|-----|-----------|---------|
| canonical | phase436 | 138,567 | 1.124 | 0 | 39 |
| day_high_a | 1.0 | 138,567 | 1.124 | -0 | 39 |
| day_high_a | 1.5 | 138,567 | 1.124 | -0 | 39 |
| day_high_a | 2.0 | 169,767 | 1.1507 | 31,200 | 29 |
| day_high_b | 1.0 | 138,567 | 1.124 | -0 | 39 |
| day_high_b | 2.0 | 138,167 | 1.1235 | -400 | 37 |
| r10_thresh | -0.1 | 138,567 | 1.124 | -0 | 39 |
| r10_thresh | -0.2 | 139,267 | 1.1246 | 700 | 38 |
| r15_thresh | -0.4 | 138,567 | 1.124 | -0 | 39 |
| r15_thresh | -0.6 | 139,167 | 1.1245 | 600 | 38 |

## Part D — Symbol contribution to improvement

- Total improvement: 90,999 yen
- 6976: 68,000 yen (0.7473)
- Top3 rate (net delta): 1.2198 (gross positive pool: 0.7317)
- Winner removal offset: -60,701 yen
- Top5 rate: 1.4561

## Part E — Daily distribution

- Improved / worsened / flat: 3 / 1 / 9
- Max improvement: 20260618 (84,200)
- Max worsened: 20260615 (-29,901)

Runtime/YAML/Entry/Exit/Order/Discord changes **forbidden** (audit only).
