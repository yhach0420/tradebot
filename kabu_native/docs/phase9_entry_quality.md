# Phase 9: ENTRY品質分析

## 目的

Phase 8 の損失改善が **trade数減少のみ** か **ENTRY品質改善** かを切り分ける。

## 対象シナリオ

| ID | 設定 |
|----|------|
| baseline | fail_window 2m, buffer 0.10, bf_confirm 1, entry from 09:00 |
| candidate_a | no_entry_until **09:30** |
| candidate_b | bf_confirm **2**, fail_buffer **0.12** |
| candidate_c | no_entry_until **09:15** |

期間: 2026-04-10 〜 2026-05-15  
（27 symbols, 540 symbol-days）

## 1. MFE到達率

| シナリオ | trades | +0.1% | +0.3% | +0.5% | +1.0% | avg MFE |
|----------|--------|-------|-------|-------|-------|---------|
| baseline | 83 | 28.9% | 6.0% | 3.6% | 1.2% | 0.094% |
| candidate_a | 56 | 28.6% | 3.6% | 1.8% | 1.8% | 0.090% |
| candidate_b | 67 | 52.2% | 22.4% | 14.9% | 6.0% | 0.235% |
| candidate_c | 62 | 25.8% | 3.2% | 1.6% | 1.6% | 0.084% |

## 2. ENTRY直後逆行（MAE）

| シナリオ | 1分 avg | 1分 adverse率 | 3分 avg | 5分 avg |
|----------|---------|---------------|---------|---------|
| baseline | -0.855% | 100.0% | -0.855% | -0.855% |
| candidate_a | -0.768% | 100.0% | -0.768% | -0.768% |
| candidate_b | -0.966% | 100.0% | -0.982% | -0.982% |
| candidate_c | -0.789% | 100.0% | -0.789% | -0.789% |

## 3. breakout継続率

| シナリオ | 高値更新率 |
|----------|------------|
| baseline | 4.8% |
| candidate_a | 7.1% |
| candidate_b | 62.7% |
| candidate_c | 6.5% |

## 4. HOLD時間

| シナリオ | avg (min) | median (min) |
|----------|-----------|--------------|
| baseline | 0.23 | 0.20 |
| candidate_a | 0.27 | 0.20 |
| candidate_b | 2.56 | 1.20 |
| candidate_c | 0.26 | 0.20 |

## 5. EXIT理由別 MFE（平均）

| シナリオ | BF | hard_stop | time_stop | vwap |
|----------|-----|-----------|-----------|------|
| baseline | 0.108% | 0.037% | — | — |
| candidate_a | 0.090% | 0.079% | — | — |
| candidate_b | 0.048% | 0.133% | — | 0.791% |
| candidate_c | 0.086% | 0.061% | — | — |

## 6. 削除された trade（baseline 比）

### vs candidate_a

- 削除: **27** / 維持: 56 / 追加: 0
- 解釈: `mostly_noise_trades_removed`
- 削除内訳: 勝ち 0件 (pnl 0.00%), 負け 27件 (pnl -27.95%)
- 削除のノイズ proxy (MFE<0.1% & 負け): 70.4%
- 同一ENTRYの成績変化（維持 56件）: avg MFE Δ +0.000%, avg PnL Δ +0.000%

### vs candidate_b

- 削除: **16** / 維持: 67 / 追加: 0
- 解釈: `mostly_noise_trades_removed`
- 削除内訳: 勝ち 0件 (pnl 0.00%), 負け 16件 (pnl -7.94%)
- 削除のノイズ proxy (MFE<0.1% & 負け): 68.8%
- 同一ENTRYの成績変化（維持 67件）: avg MFE Δ +0.150%, avg PnL Δ +0.206%

### vs candidate_c

- 削除: **21** / 維持: 62 / 追加: 0
- 解釈: `mostly_noise_trades_removed`
- 削除内訳: 勝ち 0件 (pnl 0.00%), 負け 21件 (pnl -21.99%)
- 削除のノイズ proxy (MFE<0.1% & 負け): 61.9%
- 同一ENTRYの成績変化（維持 62件）: avg MFE Δ +0.000%, avg PnL Δ +0.000%


### シナリオ別メモ

- **candidate_a**: Opening gate removes losing early entries; kept-trade MFE flat.
- **candidate_b**: BF confirm=2 extends holds; MFE/continuation up on same entries.
- **candidate_c**: Mid gate like A; trade count cut without MFE lift on survivors.

## 結論

- EXIT/HOLD改善（MFE・継続率）: **candidate_b**
- ENTRY品質（到達率）改善: **candidate_b**
- **trade数削減が主因**: **candidate_a, candidate_c**
- **次に触るべき層: EXIT (BF confirm / hold — same entries breathe longer)**

### 読み方

- MFE到達率・breakout継続率が baseline より上 → ENTRY品質向上の証拠
- total_pnl 改善は主に trade 削減で、MFE/継続率が横ばい → EXIT/ゲート側の効果
- 削除 trade の勝ちが多い → 利益機会も捨てている

## 出力ファイル

- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase9_entry_quality_20260516.csv`
- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase9_entry_quality_20260516.json`
