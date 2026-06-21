# Phase481 — PBv2 Stop Low MFE Reduction Tournament

**Verdict:** `overfit_candidate`
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最大分離特徴量 | **mae_pct** |
| 2 | 最良guard | **G7 (poor r10 AND weak vwap)** |
| 3 | PnL改善 | **12241.1** |
| 4 | PF改善 | **0.5098** |
| 5 | maxDD変化 | **0.0** |
| 6 | stop_low_mfe削減件数 | **-13** |
| 7 | stop_low_mfe削減PnL | **65300.1** |
| 8 | blocked winners | **53** |
| 9 | blocked losers | **38** |
| 10 | 6976影響 | {'guard_id': 'G7', 'symbol': '6976', 'day': 'ALL', 'accepted_count': 12, 'total_pnl_yen': 203501.28, 'stop_low_mfe_count': 1, 'stop_low_mfe_pnl_yen': -21000.0, 'delta_pnl_vs_baseline': -17500.0} |
| 11 | 4062影響 | {'guard_id': 'G7', 'symbol': '4062', 'day': 'ALL', 'accepted_count': 13, 'total_pnl_yen': 27502.72, 'stop_low_mfe_count': 1, 'stop_low_mfe_pnl_yen': -21500.0, 'delta_pnl_vs_baseline': 18501.17} |
| 12 | 6/18影響 | {'guard_id': 'G7', 'day': '20260618', 'accepted_count': 0, 'total_pnl_yen': 0.0, 'stop_low_mfe_count': 0, 'delta_pnl_vs_baseline': 0.0} |
| 13 | 6/19影響 | {'guard_id': 'G7', 'day': '20260619', 'accepted_count': 1, 'total_pnl_yen': 0.0, 'stop_low_mfe_count': 0, 'delta_pnl_vs_baseline': 0.0} |
| 14 | 過学習リスク | **high** |
| 15 | Runtime候補 | **False** |
| 16 | Shadow候補 | **G7** |
| 17 | 次アクション | Verdict: overfit_candidate; Guard improves in-sample but LOO/concentration unstable — shadow only with caution; Best PnL delta vs baseline: 12241.1 |

## Guard tournament

- **G7**: PnL 415203.92 Δ12241.1 slm Δ-13 blocked_w 53
- **baseline**: PnL 402962.82 Δ0.0 slm Δ0 blocked_w 0
- **G5**: PnL 402962.82 Δ0.0 slm Δ0 blocked_w 0
- **G9**: PnL 402962.82 Δ0.0 slm Δ0 blocked_w 0
- **G4**: PnL 388103.36 Δ-14859.46 slm Δ-20 blocked_w 71
- **G10**: PnL 370112.08 Δ-32850.74 slm Δ-7 blocked_w 20
- **G6**: PnL 221073.88 Δ-181888.94 slm Δ-16 blocked_w 64
- **G8**: PnL 214373.28 Δ-188589.54 slm Δ-19 blocked_w 69

**判定:** `overfit_candidate`