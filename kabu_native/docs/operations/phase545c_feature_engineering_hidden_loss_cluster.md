# Phase545C — Feature Engineering for Hidden Loss Cluster

**Verdict:** `phase545c_feature_engineering_hidden_loss_cluster_done`  
**対象:** Phase545B Sub1（667 trades）+ 比較コホート  
**Runtime変更:** なし

## 新規特徴量（20個）

| グループ | 特徴量 |
|----------|--------|
| Relative Board | relative_board_ratio, relative_board_delta |
| Board Collapse | board_collapse_1m/3m/5m |
| Relative Volume | relative_volume |
| Volume Acceleration | volume_accel_1m/3m/5m |
| Momentum Decay | momentum_decay_1m/3m/5m |
| Breakout Persistence | breakout_persistence_ratio |
| VWAP Recovery | vwap_recovery_min, vwap_above_sec |
| Update Burst | update_interval_median/var, update_burst_score |
| Liquidity | liquidity_burst |
| Exhaustion | exhaustion_score |

---

## 調査2 — Sub1 vs 利益型 分離（上位）

| Feature | vs | separation | Cohen's d |
|---------|-----|------------|-----------|
| **relative_volume** | cluster5 | **0.56** | -2.24 |
| **relative_volume** | sub0 | **0.47** | +1.52 |
| vwap_recovery_min | cluster5 | 0.47 | -2.17 |
| **volume_accel_5m** | sub0 | 0.45 | +1.16 |
| **volume_accel_3m** | sub0 | 0.42 | +1.01 |
| **exhaustion_score** | sub0 | 0.26 | -0.23 |

**結論:** 「状態」特徴（board_imbalance 単点）より **relative_volume / volume_accel / exhaustion** が Sub1 分離に有効。board_collapse / relative_board は event snap 欠損多く効果弱。

---

## 調査3 — Sub1 再クラスタリング

**採用:** KMeans **k=8**（silhouette=0.26, profit_separation=1157, composite=0.48）

| Method | k | Silhouette | DB | CH | profit_sep | 採用 |
|--------|---|------------|-----|-----|------------|------|
| **KMeans** | **8** | 0.260 | 1.30 | 92.4 | **1157** | ✓ |
| Hierarchical | 6 | 0.245 | 1.29 | 81.0 | 1026 | |
| KMeans | 2 | 0.272 | 1.94 | 96.6 | 450 | |

k=2 は silhouette 最高だが profit_separation が低いため k=8 を採用。

---

## 調査4 — 新SubCluster（Sub1 内部）

| Sub | ラベル | n | PF | PnL | MFE0% | BigWin% | stop% |
|-----|--------|---|-----|-----|-------|---------|-------|
| **0** | **混合サブ型（損失主因）** | 304 | **0.63** | **−242,100** | 27.0% | 14.5% | 70.7% |
| 1 | 枯渇型 | 83 | 0.94 | −4,900 | 25.3% | 16.9% | 59.0% |
| 2 | 枯渇型 | 71 | 0.66 | −16,500 | 38.0% | 8.5% | 84.5% |
| 3 | 枯渇型 | 17 | 0.06 | −37,800 | 58.8% | 5.9% | 88.2% |
| 4 | 枯渇型 | 6 | 8.82 | +13,300 | 33.3% | 33.3% | 50.0% |
| 5 | 枯渇型 | 39 | 0.49 | −22,900 | 18.0% | 15.4% | 56.4% |
| 6 | 枯渇型 | 11 | 1.05 | +100 | 54.5% | 0% | 100% |
| **7** | **モメンタム枯渇** | 136 | **1.70** | **+34,900** | 52.2% | 4.4% | 94.9% |

**最大損失は依然 Sub0（304件、−242k）** — 変化系特徴でも「平凡な塊」が残存。  
**最大利益は Sub7（モメンタム枯渇、+35k）** だが MFE0 52% / stop 95% と品質課題。

---

## 調査5 — Shadow候補

| Action | Subclusters |
|--------|-------------|
| **Shadow Reject** | 0（混合）, 2, 3, 5（枯渇損失） |
| **Shadow Bonus** | 7（モメンタム枯渇・利益）, 4（n=6 要監視） |
| Shadow Hold | 1, 6 |

---

## 必須回答（14項目）

1. **新特徴量計算:** ✅ 1309 trades に付与
2. **Sub1分離特徴:** relative_volume, volume_accel, exhaustion_score
3. **Relative Board:** ❌ 効果弱（snap欠損）
4. **Board Collapse:** ❌ 効果弱
5. **Relative Volume:** ✅ separation 0.56（最強）
6. **Momentum Decay:** ✅ 有効
7. **Breakout Persistence:** ❌ 弱い
8. **Exhaustion Score:** ✅ 有効
9. **Sub1再分離:** ✅ k=8 に分離
10. **最大損失Sub:** Sub0 混合サブ型（−242,100）
11. **最大利益Sub:** Sub7 モメンタム枯渇（+34,900）
12. **Shadow Reject:** 0, 2, 3, 5
13. **Shadow Bonus:** 7（+4 は n=6）
14. **次Phase:** phase546_entry_cluster_shadow_replay

---

## 結論

- **変化系特徴は有効** — 特に volume 相対化と acceleration が Sub1 を利益型と分離。
- **Board系は event snap 依存** で欠損率高く、本データでは弱い。
- Sub1 は **8サブクラスタ** に分離可能だが、**損失の72%は Sub0（304件）に残存** → さらなる特徴（tick-level board 系列）または Sub0 限定再帰が必要。
- Sub7 は利益だが **高MFE0/高stop** — Shadow Bonus ではなく Hold+EXIT研究が先。

## 成果物

```
results/reports/phase545c_engineered_features.csv
results/reports/phase545c_feature_separation.csv
results/reports/phase545c_recluster_summary.csv
results/reports/phase545c_subcluster_summary.csv
results/reports/phase545c_shadow_candidates.csv
results/reports/phase545c_report.json
```

## 実行

```bash
python scripts/run_phase545c_feature_engineering_hidden_loss_cluster.py
```
