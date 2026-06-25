# Phase502 — Classic Indicator Guard Replay

**Verdict:** `classic_indicator_guard_viable`
**Period:** 20260529 — 20260622

## 必須回答

| # | 回答 |
|---|------|
| 1 最良guard | **B_rsi_over80** |
| 2 delta PnL | **16289.96** |
| 3 delta PF | **0.0963** |
| 4 delta maxDD | **-10899.12** |
| 5 blocked winners | **2** |
| 6 blocked losers | **9** |
| 7 6976影響 | **0** |
| 8 4062影響 | **0** |
| 9 AM影響 | **-17799.96** |
| 10 PM影響 | **1510.0** |
| 11 LOO | **12/13 positive** |
| 12 overfit | **low** |
| 13 Replay候補 | **True** |
| 14 Shadow候補 | **True** |
| 15 Runtime候補 | **False** |
| 16 次アクション | Forward-shadow C_late_chase_AND_rsi_over80 (best combo: +15.6k, 1W/6L); avoid D/G; B standalone +16.3k but user intent is combo guards |

## Focus guards (C / D / G)

- C delta: **15599.96**
- D delta: **-47500.66**
- G delta: **-31900.7**

## 重要所見

- **B (standalone rsi_over80)** が delta 最大 (+16,290) だが、**C (late_chase AND rsi_over80)** は +15,600 で winner cut **1 vs 2** — 本命 combo として C を推奨
- **D (falling_knife AND macd_weak)**: delta **-47,501** (13W/5L cut) — **不可**
- **G (C OR D)**: delta **-31,901** — D が毒
- **A/E**: winner 過剰カットで delta マイナス
- Runtime 不採用; Shadow は **C** 優先

## All guards

| Scenario | delta PnL | PF | blocked W/L | W/L ratio |
|----------|-----------|-----|-------------|-----------|
| B_rsi_over80 | 16289.96 | 1.7134 | 2/9 | 0.2222 |
| C_late_chase_AND_rsi_over80 | 15599.96 | 1.6904 | 1/6 | 0.1667 |
| F_MST_near_high_AND_rsi_over80 | 7900.0 | 1.6679 | 1/4 | 0.25 |
| E_high_price_ext_AND_pv25_high | -17000.0 | 1.5742 | 4/0 | 4.0 |
| G_conservative_C_OR_D | -31900.7 | 1.5689 | 14/11 | 1.2727 |
| A_macd_histogram_risk_reject | -47180.51 | 1.5312 | 32/22 | 1.4545 |
| D_falling_knife_AND_macd_weak | -47500.66 | 1.5011 | 13/5 | 2.6 |

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase502_classic_indicator_guard_replay.py --parallel --max-workers 2
```
