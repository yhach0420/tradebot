# Phase470 — Momentum:low Necessity Tournament

**Verdict:** `momentum_required`  
**Period:** 20260529–20260619  
**Exit:** Hard Stop → No Progress → Board Dynamic Trailing (CAP5)

---

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | Momentum:low 必要か | **Yes — 必須** |
| 2 | 最良 variant | **A** — 現行 Baseline |
| 3 | A vs B 差分 | **−248,211** 円 / PF −0.605 / maxDD +66k / accepted +47 |
| 4 | A vs C 差分 | **−199,811** 円 (Late Chase だけでは不足) |
| 5 | B vs C 差分 | **+48,400** 円 (Late Chase は no-momentum でも改善) |
| 6 | PF改善 (best vs A) | 0 |
| 7 | maxDD変化 | 0 |
| 8 | accepted変化 | 0 |
| 9 | 6976影響 | A +221,001 / B +29,501 (**−191,500**) |
| 10 | 4062影響 | A −5,998 / C +9,002 (C only) |
| 11 | 6920影響 | 0 / 0 |
| 12 | 6/18影響 | A +14,600 / B −122,000 (**−136,600**) |
| 13 | 6/19影響 | 0 / 0 |
| 14 | 過学習 | **No** (A LOO 全正、6976 除外で符号維持) |
| 15 | Runtime候補 | **No** |
| 16 | Shadow候補 | **None** |
| 17 | 次アクション | Momentum:low 維持。Late Chase は **A 上** (Phase469 B) のみ shadow |

---

## Tournament Results

| rank | var | label | PnL | PF | maxDD | accepted | Δ vs A |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | **A** | Baseline + Momentum:low | **357,763** | **1.764** | 71,000 | 278 | — |
| 2 | C | No momentum + Late Chase | 157,952 | 1.253 | 137,000 | 299 | −199,811 |
| 3 | B | No momentum (Pullback only) | 109,552 | 1.159 | 137,000 | 325 | −248,211 |
| 4 | E | B OR D (Pullback + Trend) | 109,552 | 1.159 | 137,000 | 325 | −248,211 |
| 5 | D | Trend only T4 | −11,300 | 0.963 | 229,000 | 19 | −369,063 |

---

## 解釈

**Momentum:low は必須**
- 除去すると accepted **+47** だが PnL **−69%**、PF 1.76 → 1.16
- 主因: **6976** の捕捉喪失 (−191k)。Momentum:low が high-momentum loser を block し 6976 winner を通している

**Late Chase は Momentum 代替にならない**
- C (no momentum + Late Chase) も A より **−200k**
- Phase469 B (+45k) は **Momentum:low 維持 + Late Chase** — 本結果と整合

**Trend T4 (D) / Dual (E)**
- D: −11,300 (Phase465B 再現)
- E ≡ B — T4 が B の accepted を増やさず dual 無効

**6/18 集中**
- A: +14,600 / B: −122,000 — Momentum:low 除去で 6/18 が崩壊

---

## Robustness (A baseline)

| test | PnL | Δ vs full |
|---|---:|---:|
| full | 357,763 | — |
| exclude 6976 | 131,262 | −226,501 |
| LOO 6/02 | 250,093 | −107,670 |
| LOO 6/15 | 221,563 | −136,200 |

6976 依存は高いが、no-momentum より A が全 LOO で優位。

---

## Phase469 との関係

| Phase | B の意味 | 結果 |
|---|---|---|
| 469 | A + Late Chase (Momentum 維持) | **+45k** |
| 470 | Momentum 除去 | **−248k** |

→ Late Chase は **追加 guard** として有効、**Momentum 代替** ではない。

---

## 判定

`momentum_required` — Pullback Runtime から Momentum:low を外すと成績が大幅悪化。現行 stack を維持。

---

## 成果物

- `results/reports/phase470_momentum_necessity_tournament.csv`
- `results/reports/phase470_momentum_necessity_robustness.csv`
- `results/reports/phase470_momentum_necessity_summary.json`
