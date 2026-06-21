# Phase461 — Near Day High Guard Exception Audit

Generated: 2026-06-20T13:43:20+09:00
Period: 20260529..20260619

**Verdict:** `near_high_exception_candidate`

## Part A — Guard監査

- block count: **25625**
- guard would_pnl: **284244500.0**
- win_rate: **0.5741**
- PF: **3.0024**

## Part D — Winner vs Loser (TOP10 effect size)

| feature | winner_mean | loser_mean | delta | cohens_d |
|---|---:|---:|---:|---:|
| r5 | 0.2272 | 0.2553 | -0.0281 | -0.0414 |
| vwap_dev | 2.6195 | 2.635 | -0.0155 | -0.008 |

- 勝ち側だけが持つ特徴: []
- 負け側だけが持つ特徴: []

## Part E/F — Exception Tournament & Replay

| variant | rescued | ΔPnL | PF | maxDD | captured 3441/6492/7256/7600 |
|---|---:|---:|---:|---:|---|
| A_r15_gt0 | 0 | 18499.18 | 1.1892 | 132650.0 | N/N/N/N |
| B_r30_gt0 | 0 | 25900.0 | 1.2018 | 132650.0 | N/N/N/N |
| C_high_update_ge2 | 0 | 0.0 | 1.1743 | 151150.0 | N/N/N/N |
| D_vwap_above_ge07 | 0 | 0.0 | 1.1743 | 151150.0 | N/N/N/N |
| E_consec_above_ge20 | 0 | 0.0 | 1.1743 | 151150.0 | N/N/N/N |
| F_A_and_B | 0 | 25900.0 | 1.2018 | 132650.0 | N/N/N/N |
| G_A_and_D | 0 | 0.0 | 1.1743 | 151150.0 | N/N/N/N |
| H_B_and_D | 0 | 0.0 | 1.1743 | 151150.0 | N/N/N/N |
| I_A_and_B_and_D | 0 | 0.0 | 1.1743 | 151150.0 | N/N/N/N |
| K_r5_gt0 | 14364 | 56899.18 | 1.2577 | 121100.0 | Y/Y/Y/Y |
| L_r5_and_vwap_dev_lt2 | 5617 | -12600.0 | 1.1577 | 170600.0 | Y/Y/Y/Y |
| J_best_two_feature_combo | 5617 | -12600.0 | 1.1577 | 170600.0 | Y/Y/Y/Y |

## Mandatory answers

1. guard block件数: **25625**
2. guard PnL (close-hold proxy sum): **284244500.0** (median **1000.0**)
3. 勝ちblock: **14711**
4. 負けblock: **9058**
5. 最良例外: **K_r5_gt0**
6. PnL改善: **56899.18**
7. PF改善: **0.0834**
8. maxDD変化: **-30050.0**
9–12. 3441/6492/7256/7600: **True/True/True/True**
13. Runtime候補: **True**
14. Shadow候補: **True**
15. 次アクション: ['Shadow-test K_r5_gt0 exception on near_day_high guard before runtime', 'Review guard thresholds', 'Walk-forward validate exception on days after 6/19']
