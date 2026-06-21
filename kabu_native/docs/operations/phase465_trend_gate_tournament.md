# Phase465 — Trend Entry Gate Tournament

Generated: from `phase465_summary.json`  
Period: **20260529..20260619**  
Cohort: **Trend-following only** (29,460 candidates from Phase464)

**Verdict:** `trend_no_edge`

---

## Part B — Winner vs Loser (top features)

| rank | feature | cohens_d | MI | winner_mean | loser_mean |
|---:|---|---:|---:|---:|---:|
| 1 | high_update_count_30m | 0.59 | 0.041 | 13.08 | 8.32 |
| 2 | high_update_count_session | 0.53 | 0.034 | 41.03 | 24.41 |
| 3 | day_high_distance | −0.41 | 0.015 | 1.47 | 2.26 |
| 4 | vwap_above_ratio | 0.31 | 0.000 | 0.83 | 0.71 |
| 5 | board_imbalance | −0.31 | 0.021 | 0.49 | 0.53 |
| 6 | consecutive_above_ticks | 0.30 | 0.009 | 1058 | 728 |

Winners cluster on **more high-updates**, **closer to day-high**, **higher VWAP stability** — but replay does not monetize this.

---

## Part C/D — Trend Gate Tournament (trend-only replay)

| rank | gate | PnL | PF | accepted | cohort win_rate |
|---:|---|---:|---:|---:|---:|
| 1 | **T1** r30>0 | 0 | — | **0** | — |
| 2–5 | T6–T10 composites | 0 | — | 0 | — |
| 6 | T4 consec≥20 | −11,300 | 0.96 | 19 | 65% |
| 7 | T5 board_high | −103,800 | 0.65 | 20 | 51% |
| 8 | T3 vwap≥0.7 | −123,800 | 0.59 | 18 | 65% |
| 10 | T2 high_update≥2 | −168,200 | 0.41 | 20 | 61% |

**T1 (best by PnL rank)** accepts **0 trades** in capacity replay — Trend gate + board/drift/shape/phase364 (no momentum bypass) yields empty set on phase463 replay pool.

Gates with accepted>0 all **lose money** in trend-only replay despite positive cohort win_rate proxy.

---

## Part E — Dual architecture

| variant | PnL | PF | accepted | Δvs Pullback |
|---|---:|---:|---:|---:|
| A Pullback | 357,763 | 1.76 | 278 | 0 |
| B Trend (T1) | 0 | — | 0 | −357,763 |
| C Dual OR | 357,763 | 1.76 | 278 | 0 |

Dual adds **zero** marginal entries — Trend path never fires alongside Pullback.

Symbol capture (3441/6492/7256/7600): **all False** for A/B/C.

---

## Part F — Robustness (best gate T1)

LOO / exclude 6976 / exclude 4062: all **0 PnL, 0 accepted** — no overfit signal because there is no edge to overfit.

---

## Mandatory answers

1. Best Trend Gate: **T1** (r30>0) — trivially best because others lose
2. Trend-only PnL: **0**
3. Trend-only PF: **null**
4. Dual PnL: **357,763** (= Pullback)
5. Dual PF: **1.76**
6. 6976: Pullback +221k, Trend 0, Dual unchanged
7. 4062: Pullback −6k, Trend 0, Dual unchanged
8–11. 3441/6492/7256/7600 capture: **all False**
12. Overfit risk: **False**
13. Runtime candidate: **False**
14. Shadow candidate: **None**
15. Next: keep Pullback runtime; do not deploy trend-only gate; revisit near-high exception for 6/19 uptrend misses

---

## Conclusion

Phase464 pre-gate **would-PnL** for Trend-following was positive (close proxy, large cohort).  
Phase465 shows **no replay-monetizable Trend Entry Gate**: gates either accept nothing (T1/T6–T10) or accept trades that **lose** (T2–T5).

**判定: `trend_no_edge`** — Trend-following pre-gate signal does not survive runtime entry stack (board + guards, no momentum bypass) in capacity replay.

---

## Outputs

- `results/reports/phase465_trend_gate_tournament.csv`
- `results/reports/phase465_trend_dual_replay.csv`
- `results/reports/phase465_trend_robustness.csv`
- `results/reports/phase465_summary.json`
