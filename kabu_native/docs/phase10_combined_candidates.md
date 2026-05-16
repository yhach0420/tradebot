# Phase 10: 組み合わせ候補検証（A/C + B）

## 目的

寄り後ゲートで悪い ENTRY を削り、BF confirm=2 で過剰早逃げを抑えたとき、
全体成績が baseline / B 単独より改善するかを確認する。

期間: 2026-04-10 〜 2026-05-15（27 銘柄）

## シナリオ

| ID | no_entry_until | bf_confirm | fail_buffer |
|----|----------------|------------|-------------|
| baseline | 09:00 | 1 | 0.10 |
| B | 09:00 | 2 | 0.12 |
| A_plus_B | **09:30** | 2 | 0.12 |
| C_plus_B | **09:15** | 2 | 0.12 |

## 結果サマリー

| シナリオ | trades | win% | total_pnl | avg_pnl | PF | MFE≥0.3% | 継続率 | med hold | top sym | 採用 |
|----------|--------|------|-----------|---------|-----|-----------|--------|----------|---------|------|
| baseline | 83 | 0.0% | -70.34% | -0.847% | 0.000 | 6.0% | 4.8% | 0.20m | 9984.T |  |
| B | 67 | 13.4% | -48.58% | -0.725% | 0.051 | 22.4% | 62.7% | 1.20m | 9984.T |  |
| A_plus_B | 46 | 15.2% | -28.81% | -0.626% | 0.075 | 21.7% | 71.7% | 1.32m | 7013.T | ★ shadow |
| C_plus_B | 52 | 13.5% | -34.35% | -0.661% | 0.064 | 19.2% | 69.2% | 1.32m | 7013.T |  |

## EXIT理由分布

- **baseline**: breakout_failure=63, hard_stop=16, board_imbalance_deterioration=4
- **B**: board_imbalance_deterioration=30, hard_stop=17, breakout_failure=16, vwap_reclaim_failure=3, push_density_drop=1
- **A_plus_B**: board_imbalance_deterioration=25, breakout_failure=12, hard_stop=8, push_density_drop=1
- **C_plus_B**: board_imbalance_deterioration=27, breakout_failure=14, hard_stop=10, push_density_drop=1

## baseline / B との比較

- A+B が baseline より良い: **True**
- A+B が B より良い: **True**
- C+B が baseline より良い: **True**
- C+B が B より良い: **True**
- 寄りゲートを入れるべきか: **True**

## 結論

| 質問 | 回答 |
|------|------|
| A+B / C+B は baseline より良いか | **はい**（total_pnl -28.8 / -34.3 vs -70.3） |
| A+B / C+B は B 単独より良いか | **はい**（-28.8 / -34.3 vs -48.6） |
| 寄りゲートは入れるべきか | **はい**（B より組み合わせの方が損失・avg_pnl とも改善） |
| trade 数減少だけか | いいえ。MFE≥0.3%・継続率は B と同水準で、median hold も 1.3分前後を維持 |

**A+B** は total_pnl・avg_pnl・PF が最良。**C+B** は trade 数（52）とバランス重視の次点（pnl は A+B より約 5.5pt 悪いが baseline 比では十分改善）。

## paper_trade shadow 推奨

**A_plus_B**（共通ルール: `no_entry_until=09:30`, `bf_confirm_count=2`, `fail_buffer_pct=0.12`）

- 理由: best combined gate+BF among eligible
- trade フロア: 41（A+B は 46 で通過）
- baseline より total_pnl 改善: True
- B 単独より total_pnl 改善: True
- **保守代替**: `C_plus_B`（09:15 ゲート、52 trades）— サンプル数を増やしたい場合

### 判断メモ

- trade 数がフロア未満の設定は採用候補外
- 09:30 で trade が少なすぎる場合は 09:15（C+B）を優先
- 個別銘柄最適化は行わず、上記は全27銘柄共通ルール

## 出力

- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase10_combined_candidates_20260517.csv`
- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase10_combined_candidates_20260517.json`
