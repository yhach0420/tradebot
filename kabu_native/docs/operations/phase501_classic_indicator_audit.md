# Phase501 — Classic Technical Indicator Audit

**Verdict:** `classic_indicator_found`
**Period:** 20260529 — 20260622

## 必須回答

| # | 回答 |
|---|------|
| 1 RSI最強 | **rsi_over80** (d=0.398) |
| 2 MACD最強 | **macd_histogram_strength** (d=-0.4673) |
| 3 MA最強 | **ma5_slope** (d=0.2522) |
| 4 全体最強 | **macd_histogram_strength** (d=-0.4673) |
| 5 既存上回り | **True** (classic d=0.4673 vs existing d=0.2277) |
| 6 falling_knife | RSI14 flagged=49.5614 other=60.4651; r5 delta=-0.9457 |
| 7 high_price_extension | price_vs_25ma flagged=0.5009; RSI14 flagged=55.5556 |
| 8 late_chase | EXH_chase flagged=0.4514; RSI14 flagged=64.6565; MACD_hist flagged=4.0847 |
| 9 6976依存 | **True** |
| 10 LOO | top5_classic_loo_robust=4/5; best_classic_loo_robust=True |
| 11 overfit | **low** |
| 12 Replay候補 | **True** |
| 13 Shadow候補 | **True** |
| 14 Runtime候補 | **False** |
| 15 次アクション | Shadow-log MACD hist / rsi_over80 のみ; Runtime 不採用 |

## 重要所見

- **raw MA_200** は価格水準と混同（loser median ¥2010 vs winner ¥3937）かつ missing 88% のためランキング除外
- ランキングは **scale-free** 特徴量のみ（missing≤50%）
- **macd_histogram_strength** (|d|=0.47) は既存 **r5** (0.23) を上回るが MI≈0.002 で momentum エイリアス疑い
- **late_chase** 説明は既存 **EXH_chase_intensity** が依然として意味的に適切
- **falling_knife**: RSI14 やや低い（50 vs 60）— 既存 r5 の方が分離大
- Runtime 採用禁止方針維持 → Shadow logging のみ

## Top 15 Features

| Rank | Feature | Family | d | LOO robust |
|------|---------|--------|---|------------|
| 1 | macd_histogram_strength | macd | -0.4673 | True |
| 2 | rsi_over80 | rsi | 0.398 | True |
| 3 | ma5_slope | ma | 0.2522 | True |
| 4 | r5 | existing | 0.2277 | False |
| 5 | RSY_r5_minus_symbol_median | existing | 0.2073 | True |
| 6 | RSI14 | rsi | 0.2039 | True |
| 7 | r10 | existing | 0.1984 | False |
| 8 | EXH_chase_intensity | existing | 0.1797 | False |
| 9 | MST_near_day_high_flag | existing | 0.1591 | False |
| 10 | RSI5 | rsi | 0.1351 | False |
| 11 | rsi_over70 | rsi | 0.1308 | False |
| 12 | price_vs_10ma_pct | ma | 0.1297 | False |
| 13 | distance_from_25ma | ma | -0.1031 | False |
| 14 | rsi_under30 | rsi | -0.0888 | False |
| 15 | ma25_slope | ma | -0.0783 | False |

## 成果物

- `results/reports/phase501_classic_indicator_audit.csv`
- `results/reports/phase501_classic_indicator_ranking.csv`
- `results/reports/phase501_summary.json`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase501_classic_indicator_audit.py --parallel --max-workers 2
```
