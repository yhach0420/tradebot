# Phase545 — ENTRY Pattern Clustering & Strategy Attribution

**Verdict:** `phase545_entry_pattern_clustering_done`  
**Period:** 20260616 – 20260625（1,309 trades, Phase544 dataset）  
**Runtime変更:** なし

## クラスタリング手法比較

| Method | k | Silhouette |
|--------|---|------------|
| **KMeans（採用）** | **6** | **0.22** |
| Hierarchical | 6 | 0.21 |
| DBSCAN | 3 (+noise) | 0.24（noiseペナルティ後は不採用） |

k は 2–12 を探索し、silhouette 最良付近（−0.02以内）では **より多い k** を優先（パターン分離のため）。

---

## Cluster サマリ

| ID | ラベル | trades | 勝率 | PF | PnL | BigWin% | MFE0% | stop% | NoProg% | 保有sec |
|----|--------|--------|------|-----|-----|---------|-------|-------|---------|---------|
| 0 | **初動型** | 53 | 49.1% | 1.10 | +7,950 | **30.2%** | 20.8% | 41.5% | 0% | 411 |
| 1 | **リバウンド型（利益）** | 81 | 50.6% | **1.18** | **+31,600** | **34.6%** | 16.1% | 40.7% | 30.9% | 890 |
| 2 | **遅延追いかけ型** | 7 | 42.9% | 1.45 | +10,400 | 14.3% | 28.6% | 57.1% | 14.3% | 781 |
| 3 | **混合型（損失主因）** | 696 | 43.3% | **0.74** | **−255,400** | 12.1% | 33.8% | 75.3% | 9.3% | 359 |
| 4 | 混合型 | 288 | 43.4% | 1.07 | +23,030 | 19.4% | 27.4% | 66.7% | 6.6% | 523 |
| 5 | **リバウンド型（ダマシ）** | 184 | 32.1% | **0.49** | −45,100 | 2.2% | **60.9%** | 94.0% | 0.5% | 87 |

---

## Cluster 特徴（代表）

| ID | ラベル | 特徴 |
|----|--------|------|
| 0 | 初動型 | return_since_open 高、vwap近、update低、early minutes |
| 1 | リバウンド型（利益） | vwap_distance 高、big_winner率最高、長め保有 |
| 2 | 遅延追いかけ型 | ADX/five_min 高寄り（n=7 要再検証） |
| 3 | 混合型（損失） | 最大ボリューム、PF<1、MFE0/stop 多い |
| 4 | 混合型 | update/board_update 高、中程度PnL |
| 5 | リバウンド型（ダマシ） | MFE0 61%、超短保有、PF 0.49 |

**同名ラベル（リバウンド型）が Cluster 1（利益）と 5（損失）に分裂** → 単一特徴閾値では分離不可の証拠。

---

## 利益源 / 損失源

| 区分 | Cluster | 寄与 |
|------|---------|------|
| 利益源 #1 | **1（リバウンド型）** | +31,600（利益の 43%） |
| 利益源 #2 | 4（混合型） | +23,030（32%） |
| 利益源 #3 | 2（遅延追いかけ） | +10,400（14%、n=7） |
| **損失源 #1** | **3（混合型）** | **−255,400（損失の 85%）** |
| 損失源 #2 | 5（リバウンド型ダマシ） | −45,100（15%） |

---

## Shadow 候補（Cluster単位）

| Action | Cluster | 根拠 |
|--------|---------|------|
| **Shadow Bonus** | **1** | PF 1.18、BigWin 35%、PnL +31k |
| Shadow Reject | **3** | PF 0.74、最大損失、MFE0 34% |
| Shadow Reject | **5** | PF 0.49、MFE0 61%、短保有ダマシ |
| Hold（監視） | 0, 2, 4 | 利益だがサンプル小 or PF 境界 |

---

## 必須回答（12項目）

1. **最適Cluster数:** 6（KMeans、silhouette≈0.22）
2. **各Cluster特徴:** 上表参照（初動/リバウンド利益/遅延追い/混合損失/混合中間/リバウンドダマシ）
3. **Cluster別PF:** 0=1.10, 1=1.18, 2=1.45, 3=0.74, 4=1.07, 5=0.49
4. **Cluster別PnL:** 0=+7,950, 1=+31,600, 2=+10,400, 3=−255,400, 4=+23,030, 5=−45,100
5. **Big Winner率:** 0=30.2%, 1=34.6%, 2=14.3%, 3=12.1%, 4=19.4%, 5=2.2%
6. **MFE0率:** 0=20.8%, 1=16.1%, 2=28.6%, 3=33.8%, 4=27.4%, 5=60.9%
7. **利益源Cluster:** **1（リバウンド型・利益）**
8. **損失源Cluster:** **3（混合型）** — 全損失の約85%
9. **Shadow Reject:** **3, 5**
10. **Shadow Bonus:** **1**
11. **Runtime採用:** **No**（禁止）
12. **次Phase:** `phase546_entry_cluster_shadow_replay`

---

## 結論

- ENTRY は **少なくとも6パターン** に分離可能。単一閾値（day_return_rank / volume_percentile / ADX）では **同名パターンの利益版と損失版を分けられない**（Cluster 1 vs 5）。
- **損失の85%は Cluster 3（混合型・696 trades）** に集中。Guard/Override ではなく **Cluster 3 の Shadow Reject** が最優先。
- **利益の43%は Cluster 1** から。Shadow Bonus 候補。
- Cluster 2（遅延追いかけ）は PF 高いが **n=7** のため次 Phase で再検証。

## 成果物

```
results/reports/phase545_cluster_dataset.csv
results/reports/phase545_cluster_summary.csv
results/reports/phase545_cluster_importance.csv
results/reports/phase545_cluster_profit_source.csv
results/reports/phase545_cluster_shadow_candidates.csv
results/reports/phase545_report.json
```

## 実行

```bash
python scripts/run_phase545_entry_pattern_clustering.py
```
