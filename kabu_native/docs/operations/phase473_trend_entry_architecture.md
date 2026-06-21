# Phase473 — Trend Entry Architecture Design Audit

**Verdict:** `trend_exit_needed` (design) / dual runtime `trend_reject`  
**Period:** 20260529–20260619  
**Pullback baseline:** PBv2 (Phase472 replay equivalent)

---

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良Trend Entry | **T-B** — `consecutive_above_ticks>=20 AND vwap_dev_pct>0` + Board:mid/high |
| 2 | Trend only PnL | **−8,200** (runtime exit) |
| 3 | Trend only PF | **0.973** |
| 4 | Trend Exit改善 | **Yes** — VWAP Break: **+182,700** vs runtime −8,200 (+190,900) |
| 5 | PBv2 only PnL | **+402,963** |
| 6 | PBv2+Trend PnL | **+315,763** (−87,200 vs PBv2) |
| 7 | PBv2破壊 | **Yes** — dual CAP 競合で PBv2 トレード 3件置換、6976 −52.5k |
| 8 | 6976影響 | PBv2 +221k / Trend −66.5k / Dual +168.5k |
| 9 | 4062影響 | +9k (PBv2維持、Trend影響なし) |
| 10 | 3441捕捉 | **No** (dual/runtime) |
| 11 | 6492捕捉 | **No** |
| 12 | 7256捕捉 | **No** |
| 13 | 7600捕捉 | **No** |
| 14 | 過学習 | **Yes** — Trend top_day 45% / top_sym 45% |
| 15 | Runtime候補 | **No** — dual が PBv2 を −87k 悪化 |
| 16 | Shadow候補 | **Trend exit (VWAP break)** on frozen T-B entry — exit-only 再検証要 |
| 17 | 次アクション | PBv2 本線維持。Trend entry runtime 追加禁止。T-B + VWAP Break を Phase468 型 frozen exit audit へ |

---

## Part A — Trend候補 (runtime exit, Dynamic40 replay pool)

| id | 条件 | PnL | PF | acc |
|---|---|---:|---:|---:|
| **T-B** | CAT≥20 + vwap_dev>0 | **−8,200** | 0.973 | 19 |
| T-E2/E4 | HU30+CAT / VWAP+CAT | −11,300 | 0.963 | 19 |
| T-D | mom≥p66 + vwap≥0.7 | −44,900 | 0.859 | 20 |
| T-C/E3 | day_high≤2 + HU30 | −113,000 | 0.652 | 19 |
| T-A/E1 | HU30 + vwap≥0.7 | −123,800 | 0.592 | 18 |

T-A ≡ T-E1、T-C ≡ T-E3（同一 population）。3条件参考 (T-ref-3way) は診断のみ。

---

## Part B — Exit比較 (best: T-B)

| Exit | PnL | PF | acc | avg_hold | Δ vs runtime |
|---|---:|---:|---:|---:|---:|
| Exit-1 Runtime | −8,200 | 0.973 | 19 | 20,846s | — |
| Exit-2 VWAP Break | **+182,700** | 1.518 | 50 | 8,766s | **+190,900** |

Note: VWAP replay は shadow 差分で accepted が増える CAP 副作用あり。Exit-only 評価は Phase468 frozen set 推奨。

---

## Part C — Pullback v2 + Trend

| var | PnL | PF | acc | Δ vs PBv2 |
|---|---:|---:|---:|---:|
| A PBv2 only | **402,963** | 1.989 | 256 | — |
| B Trend only (T-B) | −8,200 | 0.973 | 19 | −411,163 |
| C PBv2 OR Trend | 315,763 | 1.746 | 253 | **−87,200** |

CAP: overlap 0 / trend_only 2 / pbv2_only 250 — 競合で PBv2 3件置換。

---

## Part D — Symbol / Day

6976: Trend runtime −66.5k（6/18 集中）。PBv2 +221k 維持が本線。  
3441/6492/7256/7600: いずれも PBv2/Trend/dual で capture なし（vwap shadow 単体では 3441 一部通過あり — CAP外）。

---

## Part E — Exit分解 (T-B losers, runtime)

Losers は主に **No Progress / Trailing** で負け。VWAP Break counterfactual は loser 集合で改善方向。Hard Stop 単体は trailing より劣後。

---

## 判定

**`trend_exit_needed`** — Entry edge は T-B で marginal (PF≈1) だが **Exit mismatch** が主因。  
**Runtime Trend entry 追加は reject** — dual が PBv2 を −87k 悪化。

---

## 成果物

- `results/reports/phase473_trend_entry_architecture.csv`
- `results/reports/phase473_trend_entry_exit_compare.csv`
- `results/reports/phase473_trend_pullback_interaction.csv`
- `results/reports/phase473_trend_symbol_day_attribution.csv`
- `results/reports/phase473_trend_exit_decomposition.csv`
- `results/reports/phase473_summary.json`
