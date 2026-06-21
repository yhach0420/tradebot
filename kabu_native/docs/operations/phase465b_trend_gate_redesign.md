# Phase465B — Trend Gate Redesign

**Verdict:** `trend_no_edge`  
**Period:** 20260529–20260619  
**Method:** high_update / VWAP / day_high only (no r-series)  
**Trend cohort:** 29,460 (Phase464 Trend-following)

Phase465 (r-series gates) was invalid. This phase re-tests gates using only features present in the cohort.

---

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良 Trend Gate | **T4** — `consecutive_above_ticks >= 20` |
| 2 | Trend-only PnL | **−11,300** 円 |
| 3 | Trend-only PF | **0.963** |
| 4 | Dual PnL (A OR B) | **270,563** 円 |
| 5 | Dual PF | **1.559** |
| 6 | 6976 影響 | PB +221,001 / Trend −66,500 / Dual +168,501 |
| 7 | 4062 影響 | PB −5,998 / Trend 0 / Dual −5,998 |
| 8 | 3441 捕捉 | **False** |
| 9 | 6492 捕捉 | **False** |
| 10 | 7256 捕捉 | **False** |
| 11 | 7600 捕捉 | **False** |
| 12 | 過学習リスク | **中** — LOO 6/18 のみ +187,900、6976 除外で +158,600 に反転 |
| 13 | Runtime 候補 | **No** |
| 14 | Shadow 候補 | **None** |
| 15 | 次アクション | Pullback 単独維持。Trend gate shadow 不採用。6976 集中と 6/18 日依存解消まで dual OR も見送り |

---

## Part A — Winner vs Loser (top by |Cohen's d|)

| rank | feature | winner_mean | loser_mean | Cohen's d | MI |
|---:|---|---:|---:|---:|---:|
| 1 | high_update_count_30m | 13.08 | 8.32 | 0.592 | 0.041 |
| 2 | high_update_count_session | 41.03 | 24.41 | 0.529 | 0.034 |
| 3 | day_high_distance | 1.47 | 2.26 | −0.406 | 0.015 |
| 4 | vwap_above_ratio | 0.833 | 0.711 | 0.309 | 0.000 |
| 5 | board_imbalance | 0.494 | 0.533 | −0.306 | 0.021 |
| 6 | consecutive_above_ticks | 1058 | 728 | 0.296 | 0.009 |

コホート would-PnL には edge シグナルあり。replay では全 gate マイナス — proxy edge は実運用に変換不可。

---

## Part B/C — Gate Tournament

| rank | gate | PnL | PF | maxDD | accepted | cohort_pass | median_would | win_proxy |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | T4 | −11,300 | 0.963 | 229,000 | 19 | 21,756 | 6,000 | 65.2% |
| 5 | T2 | −36,200 | 0.863 | 137,700 | 18 | 28,404 | 4,200 | 62.1% |
| 6 | T5 | −113,000 | 0.652 | 204,800 | 19 | 20,087 | 8,300 | 67.9% |
| 8 | T3 | −123,800 | 0.592 | 192,500 | 18 | 22,803 | 5,800 | 65.0% |
| 10 | T1 | −168,200 | 0.413 | 168,300 | 20 | 29,460 | 4,000 | 61.1% |

T4 最良だが PF < 1。T1（trend 分類基準）は最悪。

---

## Part D — Dual Replay

| variant | PnL | PF | maxDD | accepted | Δ vs Pullback |
|---|---:|---:|---:|---:|---:|
| A Pullback | **357,763** | **1.764** | 71,000 | 278 | — |
| B Trend (T4) | −11,300 | 0.963 | 229,000 | 19 | −369,063 |
| C A OR B | 270,563 | 1.559 | 89,200 | 274 | **−87,200** |

---

## Part E — Robustness (T4)

| test | PnL | Δ vs full |
|---|---:|---:|
| full | −11,300 | — |
| LOO 20260618 | +176,600 | +187,900 |
| LOO 20260617 | −107,300 | −96,000 |
| exclude 6976 | +147,300 | +158,600 |
| exclude 4062 | −11,300 | 0 |

6/18 1日集中 + 6976 依存。`trend_no_edge` 確定。

---

## 成果物

- `results/reports/phase465b_trend_gate_redesign.csv`
- `results/reports/phase465b_trend_dual_replay.csv`
- `results/reports/phase465b_trend_robustness.csv`
- `results/reports/phase465b_summary.json`