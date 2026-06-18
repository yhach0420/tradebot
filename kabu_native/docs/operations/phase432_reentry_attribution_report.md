# Phase432 — Reentry Attribution Audit

Generated: 2026-06-17T23:15:40+09:00
Target: 20260617
Verdict: **top3_dependent**

## 結論

Phase431 reentry_positive (+20401.05 yen @180s) is NOT broad market structure: positive PnL concentrates in Top3 ['186A.T', '4062.T', '5016.T'] (95% of gross winners). Top1 186A.T alone = 49% of positive leg PnL (+10100.03 yen, 1 trade). 6966.T has 9 reentry legs @180s (high churn) but net reentry PnL=-99.93 yen — frequency driver, not profit driver. After excluding Top3, residual PnL=899.99 yen (PF=1.18); edge is technically positive but economically thin. Verdict=top3_dependent: reentry 'works' on 20260617 only when 186A/4062/5016 outlier legs are included.

## Part B — Concentration (≤180s)

- total PnL: **20401.05** yen
- Top1 186A.T: **10100.03** yen (49.3% of positive)
- Top3 share (positive): **95.1%**
- Top5 share (positive): **100.0%**
- HHI: **0.3657** | Gini: **0.3768**

## Part D — 6966.T

- reentry legs @180s: 9 | zero-gap: 8
- reentry PnL @180s: **-99.93** | all-window PnL: -699.83
- assessment: **mixed_churn**

## Exclusion simulations (180s)

| scenario | count | total PnL | PF | positive |
|----------|-------|-----------|-----|----------|
| baseline_180s | 15 | 20401.05 | 5.0803 | True |
| exclude_6966 | 6 | 20500.98 | inf | True |
| exclude_top1 | 14 | 10301.02 | 3.0602 | True |
| exclude_top3 | 10 | 899.99 | 1.18 | True |
| exclude_top5 | 0 | 0.0 | 0.0 | False |

## 必須回答

- 1_top10_symbols_by_pnl_180s: [{'symbol': '186A.T', 'count': 1, 'win_rate': 1.0, 'profit_factor': inf, 'total_pnl_yen': 10100.03, 'avg_pnl_yen': 10100.03, 'median_pnl_yen': 10100.03, 'avg_hold_sec': 4532.0, 'median_hold_sec': 4532.0}, {'symbol': '4062.T', 'count': 1, 'win_rate': 1.0, 'profit_factor': inf, 'total_pnl_yen': 6501.03, 'avg_pnl_yen': 6501.03, 'median_pnl_yen': 6501.03, 'avg_hold_sec': 1305.0, 'median_hold_sec': 1305.0}, {'symbol': '5016.T', 'count': 3, 'win_rate': 1.0, 'profit_factor': inf, 'total_pnl_yen': 2900.0, 'avg_pnl_yen': 966.67, 'median_pnl_yen': 1000.08, 'avg_hold_sec': 1806.33, 'median_hold_sec': 585.0}, {'symbol': '6779.T', 'count': 1, 'win_rate': 1.0, 'profit_factor': inf, 'total_pnl_yen': 999.92, 'avg_pnl_yen': 999.92, 'median_pnl_yen': 999.92, 'avg_hold_sec': 204.0, 'median_hold_sec': 204.0}, {'symbol': '6966.T', 'count': 9, 'win_rate': 0.6667, 'profit_factor': 0.98, 'total_pnl_yen': -99.93, 'avg_pnl_yen': -11.1, 'median_pnl_yen': 499.99, 'avg_hold_sec': 574.67, 'median_hold_sec': 595.0}]
- 2_top1_share: 0.4927
- 3_top3_share: 0.9512
- 4_top5_share: 1.0
- 5_hhi: 0.3657
- 6_6966_contribution_yen: -99.93
- 7_pnl_without_6966: 20500.98
- 8_pnl_without_top3: 899.99
- 9_reentry_positive_maintained_after_top3_exclude: True
- 9b_top3_exclude_pnl_weak: True
- 10_generalizable: False