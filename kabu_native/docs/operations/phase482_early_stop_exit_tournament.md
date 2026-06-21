# Phase482 — PBv2 Early Stop / No Progress Exit Tournament

**Verdict:** `entry_problem_confirmed`
**Period:** 20260529–20260619
**Accepted trades:** 256 (PBv2 only, no enrich)
**Peak RSS:** 421.7 MB | **Output:** 0.02 MB | **Runtime:** ~29 min

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良Exit variant | **A (Baseline runtime exit)** — Hard Stop → No Progress → Board Dynamic Trailing |
| 2 | PnL改善 | **0.0** (baseline +402,962.82) |
| 3 | PF改善 | **0.0** (baseline PF 1.99) |
| 4 | maxDD変化 | **0.0** (baseline 71,000) |
| 5 | stop_low_mfe削減件数 | **0** (baseline 16件; Phase481 shadow定義42件とは tick-derived MFE で不一致) |
| 6 | stop_low_mfe削減PnL | **0.0** |
| 7 | early_exit件数 | **0** (best variant A; B/C/G は実際に early exit するが `normalize_exit_reason` が `other` に正規化し集計漏れ) |
| 8 | early_exit PnL | **0.0** |
| 9 | winner早切り | **0** (baseline A); 次点 H: cut 6 / saved 4; E/F: cut 31 |
| 10 | 6976影響 | ALL: 15件 +221,001; stop_low_mfe 0; best non-A (H) Δ0; E_N10 Δ−58,001 |
| 11 | 4062影響 | ALL: 17件 +9,002; stop_low_mfe 0; best non-A (H) Δ0; E_N10 Δ−23,002; 6/19 1件 E/F −35,500 |
| 12 | 6/18影響 | 対象7銘柄いずれも accepted 0 (baseline PnL 0); LOO 6/18 日除外 Δ−14,600 |
| 13 | 6/19影響 | 4062 のみ 1件 (baseline 0); E/F overlay で −35,500 |
| 14 | 過学習リスク | **high** — 256件・16 stop_low_mfe; LOO 不安定; 6976 が PnL の 55% |
| 15 | Runtime候補 | **False** |
| 16 | Shadow候補 | **None** |
| 17 | 次アクション | PBv2 baseline 維持; Exit overlay 不採用; stop_low_mfe は entry 側分離不可 (Phase481/482 連続確認); 6976 集中リスクは別途モニタ |
| 18 | 最大RSS | **421.7 MB** (上限 2.5 GB 未満) |
| 19 | 出力サイズ | **0.02 MB** (上限 100 MB 未満) |

**判定:** `entry_problem_confirmed`

## Baseline (A)

| metric | value |
|--------|-------|
| PnL | +402,962.82 |
| PF | 1.99 |
| maxDD | 71,000 |
| accepted | 256 |
| stop_rate | 18.4% |
| avg_hold | 2,584 s |
| stop_low_mfe | 16 (−84,800) |

## Top variants (by PnL)

| rank | variant | PnL | ΔPnL | PF | maxDD Δ | slm Δ | cut_w | saved_l |
|------|---------|-----|------|-----|---------|-------|-------|---------|
| 1 | **A** | 402,963 | 0 | 1.99 | 0 | 0 | 0 | 0 |
| 2 | H_FN10_BN5 | 376,863 | −26,100 | 2.04 | 0 | 0 | 6 | 4 |
| 3 | E_N10 | 249,790 | −153,173 | 1.54 | +6,700 | +2 | 31 | 31 |
| 4 | F_N10 | 249,790 | −153,173 | 1.54 | +6,700 | +2 | 31 | 31 |
| 5 | D_N20 | 207,190 | −195,773 | 1.47 | +600 | −7 | 40 | 47 |

## Variant family summary

- **A (Baseline):** 現行 Runtime exit が最良。変更不要。
- **E/F (Stop tighten):** stop_low_mfe を 2件増やしつつ PnL −153k〜−175k。winner 31件早切り。maxDD 悪化。
- **D (Early NP+MAE):** slm 7件削減 (D_N20) だが PnL −196k。winner 40件 cut。
- **B/C/G (Early NP / Time):** tick 1 で peak_mfe=0 → 即 exit 条件成立。avg hold ~3–7 s、PnL ~−403k。**設計上 tick 1 即発火のため tournament 候補として無効** (集計上 early_exit_count=0 は正規化バグ)。
- **H (Hybrid F+B):** 唯一 baseline に近いが Δ−26k。Runtime/Shadow 不採用。

## Robustness (baseline A)

| test | PnL | Δ vs full |
|------|-----|-----------|
| full | 402,963 | 0 |
| exclude_6976 | 181,962 | −221,001 |
| exclude_4062 | 393,961 | −9,002 |
| exclude_top_symbol | 393,961 | −9,002 |
| LOO range | 305,863 – 417,362 | −97k – +14k |

top_day_share: 8.0% | top_symbol_share: 18.1% (6976)

## 結論

Phase481 (entry guard) + Phase482 (exit overlay) の連続検証により:

1. **stop_low_mfe クラスタは entry 時点では分離不可** (outcome 変数 mfe/mae が top separator)
2. **Exit overlay も baseline を上回れない** — tighten stop は winner を過剰に cut
3. **Early NP (B/C/G) は tick 1 即発火で実用不可** — 別 Phase で tick window 定義要再検討
4. **6976 依存が高い** (PnL 55%) — exit 変更では解決しない

→ **PBv2 baseline 維持。Runtime/Exit/Entry 変更なし。**

## 実行

```bash
python scripts/run_phase482_early_stop_exit_tournament.py --parallel --max-workers 2
```

## 成果物

- `results/reports/phase482_early_stop_exit_tournament.csv`
- `results/reports/phase482_early_stop_symbol_day.csv`
- `results/reports/phase482_early_stop_robustness.csv`
- `results/reports/phase482_summary.json`
