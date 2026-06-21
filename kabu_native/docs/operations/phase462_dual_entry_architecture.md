# Phase462 — Dual Entry Architecture Audit

Generated: 2026-06-20T14:43:11+09:00
Period: 20260529..20260619

**Verdict:** `dual_entry_candidate`

## Strategy definitions

- **A Pullback:** Momentum:low + Board:mid/high + NOT High Drift + NOT Weak Shape
- **B Trend:** r15>0 AND r30>0 AND vwap_above_ratio>=0.5 AND high_update_count_30m>=2 AND Board:mid/high
- **C Dual:** A OR B

## Part C — Replay

| variant | PnL | PF | maxDD | accepted | stop_rate |
|---|---:|---:|---:|---:|---:|
| D_pullback_only | 201062.22 | 1.3604 | 60899.77 | 696 | 0.0848 |
| E_trend_only | -45310.49 | 0.5278 | 73110.3 | 53 | 0.283 |
| C_dual_entry | 216751.56 | 1.3723 | 60899.77 | 720 | 0.0931 |

## Part D — Symbol analysis

| symbol | Pullback | Trend | Dual |
|---|---|---|---|
| 6976.T | True | True | True |
| 4062.T | True | True | True |
| 3441.T | True | False | True |
| 6492.T | True | False | True |
| 7256.T | True | False | True |
| 7600.T | True | False | True |

## Part E — Missed uptrend (6/19)

| symbol | candidate | trend_pass | dual_cap | trend_cap |
|---|---|---|---|---|
| 3441.T | True | False | True | False |
| 6492.T | True | False | True | False |
| 7256.T | True | False | True | False |
| 6466.T | True | False | False | False |
| 7600.T | True | False | True | False |

## Mandatory answers

1. Pullback PnL: **201062.22**
2. Trend PnL: **-45310.49**
3. Dual PnL: **216751.56**
4. PF: Pullback **1.3604** / Trend **0.5278** / Dual **1.3723**
5. maxDD: Pullback **60899.77** / Trend **73110.3** / Dual **60899.77**
6–10. 6976/4062/3441/6492/7256: **Pullback** / **Pullback** / **Pullback** / **Pullback** / **Pullback**
11. Trend independent value: **False**
12. Dual Runtime candidate: **True**
13. Next: ['Shadow-test dual entry (Pullback OR Trend) before runtime split', 'Trend path: r15>0, r30>0, vwap_above>=0.5, high_update>=2, board mid/high', 'Walk-forward validate overlap cohort PnL on days after 6/19']
