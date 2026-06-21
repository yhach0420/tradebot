# Phase468 — Frozen Trend Exit Audit

**Verdict:** `trend_exit_problem`  
**Period:** 20260529–20260619  
**Frozen set:** Phase465B T4 accepted **19 trades** (exit-only, no CAP / no new entries)

Phase467 の capacity 混在 (F: 74件 vs A: 19件) を排除し、**同一 trade 集合**で Exit のみ比較。

---

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良 Exit | **D** — VWAP Break (price < VWAP) |
| 2 | PnL | **−2,600** 円 |
| 3 | PF | **0.58** (E が最高 PF; D は win/loss 比率で PF≈0 表示) |
| 4 | MaxDD | **2,600** 円 (D) |
| 5 | Runtime比改善 | **+91,300** 円 (A −93,900 → D −2,600) |
| 6 | 6976影響 | A −81,000 / D **0** |
| 7 | 4062影響 | 0 / 0 |
| 8 | Exit問題か | **Yes** — 同一19件で最大 +91k 改善 |
| 9 | Entry問題か | **No** (主因ではない) — 最良 Exit も依然マイナス |
| 10 | Runtime候補 | **No** — 最良 PF < 1、PnL < 0 |

---

## Exit Comparison (frozen 19 trades)

| var | label | PnL | PF | maxDD | stop_rate | avg_hold | Δ vs A |
|---|---|---:|---:|---:|---:|---:|---:|
| **D** | VWAP Break | **−2,600** | 0.58* | 2,600 | 10.5% | 37s | **+91,300** |
| E | Trend Trailing 20% | −10,900 | 0.58 | 20,000 | 26.3% | 98s | +83,000 |
| B | Session Hold | −36,700 | 0.72 | 88,500 | 84.2% | 4,229s | +57,200 |
| C | Trend Hold (stall) | −55,400 | 0.17 | 57,500 | 42.1% | 186s | +38,500 |
| A | **Runtime** | **−93,900** | 0.13 | 101,100 | 52.6% | 182s | — |

\* D の PF は `_pf` 集計上 0 表示だが、19件中 net −2,600。

---

## 解釈

**Exit 問題 (支持):**
- Runtime stack (Hard Stop → NP → Trailing) が同一 Trend 19件で **−93,900**
- Hard Stop 4件の 6976 だけで **−81,000** (A)
- VWAP Break / Session Hold / Trailing はいずれも **+38k〜+91k 改善**

**Entry 問題 (副次):**
- 最良 Exit (D) でも **PnL −2,600** — frozen set 単体では利益化未達
- Phase464 would-PnL edge は exit 改善で **縮小**するが **消滅ではない** (467) → entry+exit 両方の課題

**Session Hold (B) の注意:**
- 6278 (+55k session close), 6779 (+36k) が hold 益
- 6976 は B でも stop で −81k — hold では救えない

---

## 判定

`trend_exit_problem` — Trend edge の replay 喪失は **Exit 不整合が主因**。Entry (T4) は選別に寄与するが、**Exit 差し替えだけでは PF≥1 未達**。

次: Trend 専用 exit shadow (VWAP break / trailing 緩和) を frozen set で PF≥1 達成可否を検証。

---

## 成果物

- `results/reports/phase468_frozen_trend_exit_audit.csv`
- `results/reports/phase468_frozen_trend_exit_audit_trades.csv`
- `results/reports/phase468_frozen_trend_exit_audit.json`
