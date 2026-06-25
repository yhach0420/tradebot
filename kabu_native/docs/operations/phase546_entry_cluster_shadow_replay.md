# Phase546 — Entry Cluster Shadow Replay

**Verdict:** `phase546_entry_cluster_shadow_replay_done`  
**Period:** 20260616 – 20260625（1,309 trades, 全セッション結合）  
**Runtime変更:** なし / **採用:** なし

## Baseline（現行 Runtime 実績）

| 指標 | 値 |
|------|-----|
| trades | 1,309 |
| PnL | −227,520 |
| PF | 0.8653 |
| maxDD | 541,450 |
| MFE0 | 452 (34.5%) |
| stop_low_mfe | 948 |
| BigWinner | 189 |

---

## A. Simple Replay（Reject 単純除外）

| Variant | trades | PnL | PF | maxDD | MFE0 | lost_big | net_Δ | score | 備考 |
|---------|--------|-----|-----|-------|------|----------|-------|-------|------|
| V0 Baseline | 1,309 | −227,520 | 0.87 | 541,450 | 452 | 0 | — | 3 | |
| **V1 Cluster5 Reject** | 1,125 | −182,420 | 0.89 | 542,150 | **340** | 4 | **+45,100** | 6 | 有効 |
| V2 Cluster3 Reject | 613 | +27,880 | 1.04 | 95,700 | 217 | 84 | +255,400 | 7 | **参考のみ** |
| V3 Sub1 Reject | 642 | +48,380 | 1.06 | 96,600 | 226 | 79 | +275,900 | 7 | **参考のみ** |
| **V4 545C Loss Sub** | 878 | +91,780 | 1.10 | 102,500 | 326 | 57 | +319,300 | 7 | 有効 |
| **V5 Conservative** | 1,108 | −144,620 | 0.91 | 514,550 | 330 | 5 | +82,900 | 7 | 有効 |
| **V6 Balanced** | 694 | **+136,880** | **1.17** | **103,200** | **214** | 61 | **+364,400** | 7 | **最良** |
| V7 Bonus Only | 1,309 | −227,520 | 0.87 | 541,450 | 452 | 0 | 0 | 3 | Simple では変化なし |
| V8 Reject+Bonus | 694 | +136,880 | 1.17 | 103,200 | 214 | 61 | +364,400 | 7 | V6 と同値（Simple） |

---

## B. CAP Replay（PBv2 pool + CAP5、候補データあり）

CAP baseline（pool replay）: 188 trades, PnL −36,600（Simple baseline とは母集団が異なる点に注意）

| Variant | trades | PnL | PF | MFE0 | net_Δ vs cap基準 |
|---------|--------|-----|-----|------|------------------|
| V1 Cluster5 | 28 | +26,000 | 1.38 | 6 | +62,600 |
| V6 Balanced | 25 | +21,300 | 1.32 | 5 | +57,900 |
| V7 Bonus Only | 29 | +30,000 | 1.44 | 7 | +66,600 |

Bonus 優先（V7 CAP）で Cluster1 / Sub7 の CAP 衝突緩和に寄与。

---

## Bonus 分析

| 対象 | trades | PnL | PF | MFE0率 | BigWin |
|------|--------|-----|-----|--------|--------|
| Cluster1（リバウンド利益） | 81 | +31,600 | 1.18 | 16.1% | 28 |
| Sub7（モメンタム枯渇利益） | 136 | +34,900 | 1.70 | 52.2% | 6 |

- Bonus 対象は利益源として妥当
- Simple Replay では Bonus のみでは PnL 変化なし（想定どおり）
- CAP Replay では Bonus 優先で +66,600 改善（PBv2/OR 破壊なし）

---

## 依存性監査（Simple、抜粋）

| Variant | top10除外後 net | 6976除外 net | 支配 cluster |
|---------|----------------|--------------|--------------|
| V1 | −14,400 | +21,600 | c5 (100%) |
| V6 | +17,900 | +194,400 | c3_s0 (66%) |
| V4 | −27,200 | +172,800 | c3_s0 (76%) |

V6 は 6976 依存が高いが、除外後も net 正。

---

## 必須回答（14項目）

1. **Cluster5 Reject は有効か** → **Yes**（Simple +45,100、MFE0 −112、lost_big 4）
2. **Cluster3 Reject は強すぎるか** → **参考扱い**（retention 47% だが 696 trades・lost_big 84）
3. **Phase545C Loss SubCluster Reject は有効か** → **Yes**（+319,300、PF 1.10、MFE0 −126）
4. **Conservative Reject は有効か** → **Yes**（+82,900、lost_big 5、retention 85%）
5. **Balanced Reject は有効か** → **Yes（最良）**（+364,400、PF 1.17、MFE0 −238）
6. **Bonus Only は有効か** → **CAP のみ有効**（Simple 変化なし、CAP +66,600）
7. **Reject + Bonus は有効か** → **Yes**（Simple = V6、CAP = V6 同等）
8. **MFE0 は減るか** → **Yes**（V1 −112、V6 −238）
9. **Big Winner を削りすぎないか** → **許容内**（V6 lost_big 61 ≤ 90）
10. **CAP replay は可能か** → **Yes**（`.phase463_cache/population.pkl` 使用）
11. **最良 Variant** → **V6 Balanced Reject**
12. **Shadow 実装候補** → **V1, V4, V5, V6, V8**（V2/V3 は参考のみ）
13. **Runtime 候補** → **なし**（研究 Shadow のみ）
14. **次 Phase** → `phase547_entry_cluster_shadow_monitor`

---

## Success 条件チェック（V6 Balanced）

| 条件 | 結果 |
|------|------|
| PnL > baseline | ✅ +136,880 vs −227,520 |
| PF > baseline | ✅ 1.17 vs 0.87 |
| maxDD ≤ baseline | ✅ 103,200 vs 541,450 |
| MFE0_count < baseline | ✅ 214 vs 452 |
| stop_low_mfe < baseline | ✅ 463 vs 948 |
| lost_big_winner 許容 | ✅ 61 ≤ 90 |
| trade_count 極端減少なし | ✅ 53% retention |
| 依存性 | ⚠ 6976 寄与大（除外後も net 正） |
| 説明可能性 | ✅ Cluster5 ダマシ + 545C 損失 Sub 除外 |

---

## 出力

- `results/reports/phase546_shadow_replay_summary.csv`
- `results/reports/phase546_shadow_replay_detail.csv`
- `results/reports/phase546_shadow_replay_dependency.csv`
- `results/reports/phase546_bonus_analysis.csv`
- `results/reports/phase546_report.json`
