# Phase457 — VWAP Structure Robustness Audit

Generated: 2026-06-20T01:36:07+09:00  
Period: 20260529..20260619  
Compare: **A baseline** (Runtime) vs **B D4** (`consecutive_above_ticks < 20.5` AND `vwap_dev_pct < 0.20875`)

Research only — no Runtime / YAML / Entry / Exit / Order / Discord changes.

**Verdict:** `high_concentration`

---

## Mandatory answers

| # | Answer |
|---|--------|
| 1 | **改善日数:** 8 |
| 2 | **悪化日数:** 2 |
| 3 | **最大改善日:** **20260609** (+23,800 yen) — **6/18依存ではない** |
| 4 | **最大悪化日:** 20260601 (−6,500 yen) |
| 5 | **LOO平均delta:** **+44,204 yen** |
| 6 | **LOO中央値delta:** **+44,401 yen** |
| 7 | **top_day_share:** **0.502** (6/9が総改善の50%) |
| 8 | **top_symbol_share:** **0.327** (6976) |
| 9 | **6976寄与率:** **32.7%** (+15,500 / +47,401) |
| 10 | **4062寄与率:** **26.4%** (+12,501 / +47,401) |
| 11 | **18 block:** **11L / 7W** (blocked_pnl net −47,801) |
| 12 | **過学習判定:** **Medium** — LOOは堅牢だが単日集中度50%超 |
| 13 | **Runtime候補:** **No** (直接Runtime投入不可) |
| 14 | **Shadow候補:** **Yes** — LOO median +44k、forward windowも全改善/不変 |
| 15 | **次アクション:** Shadow-only D4 → Phase457B walk-forward |

---

## Part A — 日別寄与

| day | baseline | D4 | delta |
|-----|----------|-----|-------|
| 20260609 | 43,600 | 67,400 | **+23,800** |
| 20260610 | 23,703 | 34,203 | +10,501 |
| 20260617 | 17,300 | 25,000 | +7,700 |
| 20260601 | 289 | −6,211 | −6,500 |
| 20260618 | −15,700 | −13,700 | +2,000 |
| 20260619 | −54,850 | −52,750 | +2,100 |

- 改善 8 / 悪化 2 / 不変 3
- **6/18・6/19は改善方向** (+2k each) だが、最大寄与は **6/9**

---

## Part B — LOO (Leave One Day Out)

| Metric | Value |
|--------|-------|
| mean delta | +44,204 |
| median delta | +44,401 |
| min delta (worst exclusion) | +16,901 |
| max delta | +64,451 |
| positive LOO runs | 14 / 14 |

**解釈:** どの1日を除外しても delta > +17k。**日除外ベースの過学習ではない。**  
ただし 6/9 除外時 delta 最大 (+64k) → 6/9 block が最大の利益源。

---

## Part C — 銘柄寄与 TOP5

| symbol | baseline | D4 | delta |
|--------|----------|-----|-------|
| 6976 | +150,500 | +166,000 | **+15,500** |
| 4062 | −40,998 | −28,497 | **+12,501** |
| 4588 | −3,600 | −1,600 | +2,000 |
| 4422 | +3,000 | +4,500 | +1,500 |
| 6981 | +5,500 | 0 | −5,500 |

top_symbol_share = 6976 (32.7%) < 50% → 銘柄集中は許容範囲。

---

## Part D — 6976 / 4062 / 6920

| Symbol | blocks | loss removed | win removed | ΔPnL | 寄与率 |
|--------|--------|--------------|-------------|------|--------|
| 6976 | 1 | −21,000 (6/9) | 0 | +15,500 | 32.7% |
| 4062 | 1 | −12,501 (6/10) | 0 | +12,501 | 26.4% |
| 6920 | 0 | — | — | 0 | 0% |

両銘柄とも **損失1件の除去** が改善の主因。勝ち除去は6976/4062ではゼロ。

---

## Part E — 18 block 品質

| symbol | day | pnl | outcome |
|--------|-----|-----|---------|
| 6976.T | 6/9 | −21,000 | **loss** |
| 4062.T | 6/10 | −12,501 | **loss** |
| 4588.T | 6/9 | −3,600 | loss |
| 6327.T | 6/17 | −7,500 | loss |
| 9256.T | 6/8, 6/18 | −1,000, −3,300 | loss |
| 6981.T | 6/1 | +5,500 | win |
| … | | | **11L / 7W** |

大きなloss block: 6976 (−21k), 4062 (−12.5k) → D4の核心。

---

## Part F — Forward Stability (6/15–6/19)

| day | delta |
|-----|-------|
| 6/15 | +4,900 |
| 6/16 | 0 |
| 6/17 | +7,700 |
| 6/18 | +2,000 |
| 6/19 | +2,100 |
| **合計** | **+16,700** |

Forward window でも一貫改善。**6/18単日依存ではない。**

---

## 判定まとめ

| 観点 | 結果 |
|------|------|
| 6/18依存 | **No** — max day 6/9 |
| 特定銘柄依存 | **Partial** — 6976+4062で59%だがLOO robust |
| LOO | **Strong** — median +44k |
| 日集中 | **High** — 6/9 = 50% of delta |
| 推奨 | **Shadow-only** → walk-forward後にRuntime検討 |

---

## Outputs

- `results/reports/phase457_vwap_structure_robustness.csv`
- `results/reports/phase457_vwap_structure_daily.csv`
- `results/reports/phase457_vwap_structure_symbol.csv`
- `results/reports/phase457_vwap_structure_summary.json`

Run: `python scripts/run_phase457_vwap_structure_robustness.py`
