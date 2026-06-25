# Phase545B — Recursive Cluster Refinement (Cluster3)

**Verdict:** `phase545b_recursive_cluster_refinement_done`  
**入力:** Phase545 Cluster3（696 trades）  
**Runtime変更:** なし

## 手法比較（k=2〜8）

| Method | k | Silhouette | Davies-Bouldin | Calinski-Harabasz | Composite | 採用 |
|--------|---|------------|----------------|-------------------|-----------|------|
| **GMM** | **2** | **0.393** | 2.42 | 31.2 | **0.280** | **✓** |
| KMeans | 2 | 0.191 | 2.08 | 91.3 | 0.229 | |
| KMeans | 6 | 0.157 | 1.64 | 79.6 | 0.224 | |
| Hierarchical | 4 | 0.148 | 1.72 | 67.1 | 0.211 | |
| DBSCAN（参考） | 2 | 0.765 | 0.29 | 302.9 | 0.728* | |

\* DBSCAN は noise 構造の参考値。本採用は GMM k=2（composite 最良）。

**最適再クラスタ数: k=2** — Cluster3 は実質 **2系統** に分離。

---

## サブクラスタサマリ

| Sub | ラベル | n | 勝率 | PF | PnL | BigWin% | MFE0% | stop% | 損失寄与 | 利益寄与 |
|-----|--------|---|------|-----|-----|---------|-------|-------|----------|----------|
| **0** | **遅延追いかけ** | 29 | 44.8% | **1.44** | **+20,500** | **17.2%** | 31.0% | 69.0% | 4.8% | 9.4% |
| **1** | **混合サブ型（損失主因）** | 667 | 43.2% | **0.70** | **−275,900** | 11.8% | **33.9%** | 75.6% | **95.2%** | 90.7%* |

\* Sub1 内の個別勝ちトレードが Cluster3 内利益の大部分を占めるが、ネットは大幅損失。

---

## サブクラスタ特徴

### Sub0 — 遅延追いかけ（利益）

- price_acceleration 高、return_since_open 高
- vwap_distance 高（+1.18% vs 全体 +0.22%）
- minutes_from_open 長め（231分 vs 76分）
- trend up 寄り
- **解釈:** 遅いがモメンタム残存の追いかけ。少数だが PF>1

### Sub1 — 混合サブ型（損失）

- ボリューム最大（667 trades = Cluster3 の 96%）
- ADX/five_min/volume は全体中央値付近（「平均的な悪いENTRY」）
- board/update は中程度
- **解釈:** 明確な単一特徴ではなく「量の問題」— 典型的な混合損失群

---

## Shadow 候補

| Action | Sub | 根拠 |
|--------|-----|------|
| **Shadow Bonus** | **0（遅延追いかけ）** | PF 1.44、PnL +20.5k（n=29 要監視） |
| **Shadow Reject** | **1（混合サブ型）** | PF 0.70、損失寄与 95%、MFE0 34% |
| Shadow Hold | — | Sub1 内部のさらなる分離は Phase545C 候補 |

---

## 必須回答（10項目）

1. **最適再クラスタ数:** k=2（GMM、composite=0.28）
2. **Cluster3分割数:** **2種類**（遅延追いかけ / 混合サブ型）
3. **最大損失サブクラスタ:** Sub1 混合サブ型（−275,900、損失寄与 95%）
4. **最大利益サブクラスタ:** Sub0 遅延追いかけ（+20,500）
5. **MFE0最多:** Sub1（33.9%）
6. **BigWinner最多:** Sub0（17.2%）
7. **Shadow Reject:** Sub1
8. **Shadow Bonus:** Sub0
9. **Runtime採用:** **No**
10. **次Phase:** `phase546_entry_cluster_shadow_replay`（Sub1 追加分離は `phase545c_subcluster1_refinement` 候補）

---

## 結論

- Cluster3「混合型」は **2系統** に分解可能。
- **損失の95%は Sub1（667 trades）** — 特徴は全体中央値付近で「平凡な悪いENTRY」の塊。
- **利益は Sub0（29 trades）** の遅延追いかけに集中 — 全体の4%だが PF 1.44。
- **Sub1 の更なる分離には k≥4 が必要**（method_compare 参照）だが silhouette/composite は k=2 が最良。次は Sub1 限定の再帰 or shadow replay で検証。

## 成果物

```
results/reports/phase545b_cluster3_dataset.csv
results/reports/phase545b_cluster3_summary.csv
results/reports/phase545b_cluster3_importance.csv
results/reports/phase545b_cluster3_shadow_candidates.csv
results/reports/phase545b_cluster3_method_compare.csv
results/reports/phase545b_report.json
```

## 実行

```bash
python scripts/run_phase545b_recursive_cluster_refinement.py
```
