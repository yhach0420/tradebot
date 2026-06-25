# Phase513 — Classic Momentum Forward Shadow

**Verdict:** `phase513_momentum_shadow_done`
**Strategy:** `CLASSIC_MOMENTUM_SHADOW`

## Rules

- ENTRY: RSI14 > 50 AND Stoch K > D
- EXIT: session_end_only + hard_stop -1.2%
- Shadow only — no Runtime adoption

## Cumulative vs BASELINE

| | Shadow | BASELINE |
|--|--------|----------|
| PnL | 503010.0 | 214959.61 |
| PF | 1.8777 | 1.3476 |
| Trades | 184 | 440 |

## Mandatory answers

1. PF>1 sustained: **True** (cumulative PF=1.8777)
2. PnL positive sustained: **True**
3. 6976 dependency gone: **False** (share=81.21%)
4. single_symbol_dependency resolved: **False**
5. single_day_dependency resolved: **False**
6. session_end win pattern: **True** (wr=0.8462)
7. days beating PBv2: **7**
8. verdict: **classic_candidate_fragile**

## Robustness

- top10 trade share: 71.14%
- top1 symbol share: 81.21% (6976)
- top1 day share: 67.95%
- Gini: 0.7022
