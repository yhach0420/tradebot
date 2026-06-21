# Phase480 — PBv2 Loss Cluster Audit + Trend Shadow

**Verdict:** `pbv2_loss_reduction_candidate`
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最大損失クラスター | **A stop_low_mfe** |
| 2 | 改善余地特徴量 | **stop_hit** |
| 3 | 4062改善候補 | **time-based exit for long losers** |
| 4 | 6920改善候補 | **no PBv2 trades in period** |
| 5 | Trend shadow PnL | **44900.0** |
| 6 | Trend shadow PF | **1.0563** |
| 7 | 6976依存率 | **0.111** |
| 8 | Runtime候補 | **False** |
| 9 | Shadow継続 | **shadow_trend_candidate** |
| 10 | 次アクション | Verdict: pbv2_loss_reduction_candidate; Target cluster stop_low_mfe (0.5769 of bottom20); 4062: time-based exit for long losers; Trend shadow: record-only (PB5 Session Hold); no runtime adoption |

## Loss cluster ranking

- **1. stop_low_mfe**: 30 trades, PnL -195600.1, share 0.5769
- **2. long_hold_bleed**: 7 trades, PnL -96400.0, share 0.1346
- **3. low_momentum_entry**: 10 trades, PnL -54299.18, share 0.1923
- **4. stop_gave_back**: 2 trades, PnL -11600.0, share 0.0385
- **5. far_from_day_high**: 3 trades, PnL -9000.35, share 0.0577

**判定:** `pbv2_loss_reduction_candidate`