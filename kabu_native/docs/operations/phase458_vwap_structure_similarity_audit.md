# Phase458 — VWAP Structure Similarity Audit

Generated: 2026-06-20  
Period: 20260529..20260619  
Seeds: **18 D4-blocked trades**

Research only — no Runtime changes.

**Verdict:** `symbol_specific_pattern`

---

## Mandatory answers

| # | Answer |
|---|--------|
| 1 | **類似案件数:** **76** (k=8 NN, deduped) |
| 2 | **類似案件PnL:** **−67,790 yen** (38L / 36W) |
| 3 | **6976除外後delta:** **+24,301** (full +47,401 → −23,100) |
| 4 | **4062除外後delta:** **+37,500** (−9,901 vs full) |
| 5 | **両方除外後delta:** **+11,800** (−35,601 vs full) |
| 6 | **構造パターンか:** **Yes (feature space)** — 37銘柄に類似、net loss; **No (PnL lift)** — 75%が6976+4062依存 |
| 7 | **Runtime候補:** **No** — 両方除外後 +11.8k のみ |

---

## 特徴量ベクトル (8次元)

`r5, r10, r15, r30, vwap_dev, consecutive_above_ticks, day_high_distance, board_imbalance`

Z-score正規化 → ユークリッド距離 → runtime-eligible pool から k=8 最近傍。

---

## Seed 内訳 (18件 / 15銘柄)

6976×1, 4062×1, 4588×2, 9256×2, 6962×2, その他各1。

**6976専用ではない** — block自体は15銘柄に分散。

---

## 類似案件分析

| Metric | Value |
|--------|-------|
| Unique similar cases | 76 |
| Unique symbols | 37 |
| Aggregate PnL | −67,790 |
| Loss / Win | 38 / 36 |

類似ベクトルを持つ案件は **市場横断で net loss** → VWAP structure weakness は **汎用特徴**。

---

## D4 再評価 (銘柄除外)

| Scenario | ΔPnL vs baseline |
|----------|------------------|
| Full D4 | +47,401 |
| 6976 guard skip | +24,301 |
| 4062 guard skip | +37,500 |
| Both skip | +11,800 |

6976+4062 block 2件の除去が総改善の **~75%** を説明。

---

## 判定

| 観点 | 結論 |
|------|------|
| 6976専用? | **No** — seeds 15 symbols |
| 構造パターン (k-NN)? | **Yes** — 76 cases, 37 symbols, −67k |
| PnL lift 汎用性? | **No** — excl-both +11.8k only |
| Verdict | **`symbol_specific_pattern`** (PnL attribution) |

**解釈:** D4 guard は **構造的に正しいフィルタ** だが、in-sample PnL改善の大部分は **6976/4062 の2 loss block** に集中。Shadow継続、Runtime直接投入は Phase457 と同様 **非推奨**。

---

## Outputs

- `results/reports/phase458_vwap_structure_similarity.csv`
- `results/reports/phase458_vwap_structure_summary.json`

Run: `python scripts/run_phase458_vwap_structure_similarity_audit.py`
