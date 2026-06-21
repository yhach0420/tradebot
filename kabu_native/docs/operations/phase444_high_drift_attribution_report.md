# Phase444 — High Drift Attribution Audit

Generated: 2026-06-19T00:22:57+09:00
Verdict: **high_drift_loss_avoidance**
Period: 20260529..20260618

## Part A — High Drift rejects

- reject_count: 45
- reject_pnl_sum (would_pnl): -117499.42
- reject_win_rate: 0.4667
- reject_pf: 0.4575
- baseline_accepted subset: 39 trades, pnl -90999.42, pf 0.4876

## Part B — Added accepts (CAP / buying power freed)

- added_count: 10
- added_pnl_sum: -13500.0
- from_cap_reject: 7
- from_buying_power_reject: 3

## Part C — Improvement decomposition

- total improvement (B−A): 75899.42 yen (ref 75899.42)
- loss_avoided: 89399.42 yen (1.1779)
- additional_profit: -13500.0 yen (-0.1779)
- path_interaction (sizing): 0.0 yen

## Part D — 6/18 analysis

- baseline day pnl: -98200.0
- high drift day pnl: -37700.0
- day delta: 60500.0
- loss_avoided (day): 84200.0
- additional_profit (day): -23700.0

### Symbol contributions (6976 / 3110 / 6981)

| Symbol | Removed | Added | Loss avoided | Add profit | Net | Share |
|--------|---------|-------|--------------|------------|-----|-------|
| 6976.T | 4 | 1 | 51500.0 | -26500.0 | 25000.0 | 0.4132 |
| 3110.T | 1 | 0 | 25000.0 | 0 | 25000.0 | 0.4132 |
| 6981.T | 0 | 0 | 0 | 0 | 0 | 0.0 |

## Mandatory answers

1. reject件数: 45
2. reject PnL合計: -117499.42
3. reject PF: 0.4575
4. 追加採用件数: 10
5. 追加採用PnL: -13500.0
6. 損失回避割合: 1.1779
7. 追加利益割合: -0.1779
8. 6/18改善主因: 損失回避（ブロック）
9. High Drift本質: 損失回避型
10. 次テーマ: 6976.T 型パターンの guard 閾値感度と forward 再現性
