# Phase467 — Trend Exit Audit

**Verdict:** `trend_exit_candidate`  
**Period:** 20260529–20260619  
**Entry (fixed):** Phase465B T4 — `consecutive_above_ticks >= 20`

Phase464 Trend-following would-PnL PF 2.25 vs Phase465B replay PF 0.96 — this audit separates entry edge from exit stack mismatch.

---

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良 Exit | **F** — Hold Until Session End (hard stop only → session close) |
| 2 | PnL改善 (vs A) | **+72,200** 円 |
| 3 | PF改善 (vs A) | **+0.242** (0.963 → 1.205) |
| 4 | maxDD変化 | **−115,800** 円 (229k → 113k) |
| 5 | 6976影響 | A −66,500 / F −81,000 |
| 6 | 4062影響 | A 0 / F 0 |
| 7–10 | 3441/6492/7256/7600 | F では **未捕捉** (B/C/D/E は一部捕捉) |
| 11 | Exit改善で利益化可能か | **Yes** — F で +60,900 PnL |
| 12 | Trend edge は幻想か | **No** — exit 不整合が主因 |
| 13 | Runtime候補 | **Yes** (F shadow) |
| 14 | Shadow候補 | **F** — session-hold trend exit |
| 15 | 次アクション | Hard Stop 損失寄与を shadow 検証。F は capacity 74件 vs A 19件 — exit-only frozen set でも再確認 |

---

## Part A — Exit Attribution (Runtime A, tick sim on 19 accepted)

| bucket | count | PnL | PF |
|---|---:|---:|---:|
| **Hard Stop** | **10** | **−103,600** | 0.0 |
| No Progress | 1 | −100 | 0.0 |
| Board Dynamic Trailing | 8 | +9,800 | 3.51 |
| Session End | 0 | 0 | — |
| Other | 0 | 0 | — |

**最も損失寄与:** **Hard Stop** (−103,600 円 / 10件)。Board Dynamic Trailing は +9,800 で唯一の正寄与。

---

## Part B/C — Counterfactual Exit Replay

| variant | label | PnL | PF | maxDD | accepted | stop_rate | avg_hold | ΔvsA |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **F** | session hold | **+60,900** | **1.205** | 113,200 | 74 | 77.0% | 4,945s | **+72,200** |
| A | runtime | −11,300 | 0.963 | 229,000 | 19 | 0% | 21,597s | — |
| B | VWAP break | −28,500 | 0.911 | 229,000 | 39 | 5.1% | 10,107s | −17,200 |
| C | high-update stall | −37,600 | 0.955 | 147,500 | 538 | 13.6% | 276s | −26,300 |
| D | MFE giveback 20% | −203,500 | 0.754 | 219,700 | 805 | 12.2% | 218s | −192,200 |
| E | MFE giveback 30% | −181,600 | 0.755 | 192,200 | 763 | 11.9% | 200s | −170,300 |

**解釈:**
- Pullback向け Runtime stack (Hard Stop → NP → Board Trailing) は Trend に不整合
- Hard Stop が trend winner を過剰に刈り取る (Part A)
- Session hold (F) のみ PF>1 — ただし **accepted 74 vs 19** で capacity 解放効果が混在
- 早期 exit (C/D/E) は capacity 過剰解放 → 低品質 entry 増 → 悪化

---

## Part D — Symbol Analysis (replay)

| symbol | A PnL | F PnL | B PnL | C PnL |
|---|---:|---:|---:|---:|
| 6976 | −66,500 | −81,000 | −66,500 | +37,500 |
| 4062 | 0 | 0 | 0 | +14,000 |
| 3441 | — | — | captured | captured |
| 7256 | — | — | — | captured |

6976 は F でも損失。exclude 6976 で F **+134,400** に改善。

---

## Part E — Robustness (best = F)

| test | PnL | Δ vs full |
|---|---:|---:|
| full | +60,900 | — |
| LOO 20260618 | +141,200 | +80,300 |
| LOO 20260617 | −49,200 | −110,100 |
| exclude 6976 | **+134,400** | +73,500 |

6/17 依存あり。6976 除外で符号・規模が改善 — 銘柄集中リスク中。

---

## 判定

| 仮説 | 結論 |
|---|---|
| Trend edge なし | **棄却** — F で PF 1.20 / +61k |
| Exit 不整合 | **支持** — Hard Stop が主損失、Trailing は正 |
| Entry 問題 | 副次 — T4 gate は有効だが runtime exit が edge を消す |

**Verdict:** `trend_exit_candidate` — Trend 専用 exit shadow (session hold / stop-only) を次フェーズで frozen-set 検証。

---

## 成果物

- `results/reports/phase467_trend_exit_audit.csv`
- `results/reports/phase467_trend_exit_replay.csv`
- `results/reports/phase467_trend_robustness.csv`
- `results/reports/phase467_summary.json`
