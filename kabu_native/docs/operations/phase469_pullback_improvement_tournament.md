# Phase469 — Pullback Improvement Tournament

**Verdict:** `pullback_improvement_candidate`  
**Period:** 20260529–20260619  
**Baseline:** Pullback Runtime (Momentum:low + Board:mid/high + High Drift + Weak Shape)  
**Replay:** CAP5, current Exit stack (NP)

---

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良 variant | **B** — Late Chase Guard (Phase455) |
| 2 | PnL改善 | **+45,200** 円 (357,763 → 402,963) |
| 3 | PF改善 | **+0.225** (1.764 → 1.989) |
| 4 | maxDD改善 | **0** (71,000 維持) |
| 5 | 6920影響 | 0 / 0 (replay 期間未捕捉) |
| 6 | 6976影響 | 221,001 / 221,001 (不変) |
| 7 | 4062影響 | −5,998 → **+9,002** (+15,000) |
| 8 | 6/18改善 | 0 |
| 9 | 6/19改善 | 0 |
| 10 | 過学習 | **No** (top_day 17%, LOO 全正) |
| 11 | Runtime候補 | **Yes** — B shadow |
| 12 | Shadow候補 | **B** |
| 13 | 次アクション | B を shadow 投入。D/C OR guard は却下。6976 依存を監視 |

---

## Tournament Results

| rank | var | label | PnL | PF | maxDD | accepted | Δ vs A |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | **B** | Late Chase Guard | **402,963** | **1.989** | 71,000 | 256 | **+45,200** |
| 2 | A | Baseline | 357,763 | 1.764 | 71,000 | 278 | — |
| 3 | E | Late Chase AND VWAP | 357,763 | 1.764 | 71,000 | 278 | 0 |
| 4 | F | Near High Exception | 357,763 | 1.764 | 71,000 | 278 | 0 |
| 5 | H | (B AND C) + Near High | 357,763 | 1.764 | 71,000 | 278 | 0 |
| 6 | D | Late Chase OR VWAP | 220,763 | 1.487 | 105,800 | 259 | −137,000 |
| 7 | G | (B OR C) + Near High | 220,763 | 1.487 | 105,800 | 259 | −137,000 |
| 8 | C | VWAP Structure D4 | 175,563 | 1.342 | 105,800 | 281 | −182,200 |

---

## 解釈

**B (Late Chase Guard) — 採用候補**
- Block: `r10 < 0.3719 AND day_high_distance < 1.1872` (Phase455 best combo)
- 22件 block → +45k PnL, PF +0.22, maxDD 不変
- 4062 を −6k → +9k に改善

**C / D / G — 却下**
- VWAP D4 guard (`consecutive_above < 20.5 AND vwap_dev < 0.21`) 単独は **−182k** 悪化
- OR 合成 (D/G) も **−137k** — 過剰ブロック

**E / F / H — 効果なし (replay pool)**
- Near High Exception (r5>0 rescue): guard reject が replay pool に未注入のため A と同一
- AND 合成 (E/H): VWAP guard が発火せず B-only と同集合にならず、実質 baseline 同等

---

## Robustness (best = B)

| test | PnL | Δ vs full |
|---|---:|---:|
| full | 402,963 | — |
| LOO 6/11 | 216,863 | −186,100 |
| exclude 6976 | 176,462 | −226,501 |
| exclude 4062 | 393,961 | −9,002 |

6976 依存度が高い (exclude で −226k)。6/11 LOO 弱点あり — walk-forward 必須。

---

## 判定

`pullback_improvement_candidate` — **B (Late Chase Guard)** のみ baseline を有意改善。VWAP D4 / OR 合成は有害。

---

## 成果物

- `results/reports/phase469_pullback_improvement_tournament.csv`
- `results/reports/phase469_pullback_improvement_robustness.csv`
- `results/reports/phase469_pullback_improvement_summary.json`
