# Phase543D — Guard Override Final Tuning (Board + Volume)

**Verdict:** `phase543d_guard_override_final_tuning_done`  
**Period:** 20260616 – 20260625 (all sessions, 1,309 trades)  
**Runtime変更:** なし / **採用:** なし / **全期間検証のみ**

## 目的

Guard (G_A / G_B / G_C) は固定し、Override O1–O10 のみ最終チューニング。  
特に **Trade retention ≥30%** と **Lost Big Winner ≤90** の同時達成を検証。

## Guards

| ID | Rule |
|----|------|
| G_A | ADX ≤ 35 |
| **G_B** | ADX ≤ 35 AND five_min_position ≤ 50 |
| G_C | ADX ≤ 30 AND five_min_position ≤ 50 |

## Overrides

| ID | Rule |
|----|------|
| O1 | board ≥ 0.60 |
| O2 | board ≥ 0.55 AND volume_percentile ≥ 80 |
| O3 | board ≥ 0.50 AND volume_percentile ≥ 90 |
| O4 | board ≥ 0.55 AND volume_ratio ≥ 1.8 |
| O5 | board ≥ 0.55 AND day_leader_proxy |
| O6 | board ≥ 0.55 AND open_strength_proxy |
| O7 | board ≥ 0.50 AND volume_percentile ≥ 80 AND day_leader_proxy |
| O8 | board ≥ 0.55 AND high_update_recent |
| O9 | board ≥ 0.55 AND prior_high_break |
| O10 | board ≥ 0.55 AND volume_percentile ≥ 80 AND high_update_recent |

Proxies (entry時点のみ):
- `day_leader_proxy`: day_return_rank ≤ 20 AND volume_percentile ≥ 70
- `open_strength_proxy`: minutes_from_open ≤ 120 AND entry_rise_5min_pct > 0.2（欠損時は minutes ≤ 90 AND day_return_rank ≤ 40）

## G_B 本命結果（比較基準: G_B+O1）

| Strategy | PnL | PF | maxDD | trades | retention | MFE0 | lost_big | reintro MFE0 | recovered_big |
|----------|-----|-----|-------|--------|-----------|------|----------|--------------|---------------|
| **G_B+O1** | **+116,550** | **1.36** | **53,800** | 373 | **28.5%** | **121** | **115** | **10** | **34** |
| G_B+O2 | +21,500 | 1.06 | 53,700 | 344 | 26.3% | 119 | 131 | 8 | 18 |
| G_B+O3 | −13,700 | 0.97 | 69,200 | 449 | **34.3%** | 158 | 119 | **47** | 30 |
| G_B+O4 | +50,400 | 1.26 | 51,000 | 294 | 22.5% | 111 | 149 | 0 | 0 |
| G_B+O5 | +15,800 | 1.07 | 56,000 | 306 | 23.4% | 114 | 145 | 3 | 4 |
| G_B+O6 | +58,800 | 1.26 | 51,000 | 307 | 23.5% | 111 | 144 | 0 | 5 |
| G_B+O7 | +13,700 | 1.06 | 59,000 | 318 | 24.3% | 117 | 143 | 6 | 6 |
| G_B+O8 | +67,900 | 1.32 | 51,000 | 300 | 22.9% | 111 | 147 | 0 | 2 |
| G_B+O9 | +24,750 | 1.11 | 51,000 | 299 | 22.8% | 112 | 148 | 1 | 1 |
| G_B+O10 | +62,600 | 1.30 | 51,000 | 298 | 22.8% | 111 | 148 | 0 | 1 |

Baseline (no guard): PnL −227,520 / PF 0.87 / maxDD 550,700 / MFE0 452

## 必須回答（10項目）

### 1. boardだけでは不足だったか

**はい。** 高ADX負け銘柄のうち volume_surge / day_leader 系は board≥0.60 単独では救えず、O2/O3/O7 で retention は上がるが lost_big または reintroduced MFE0 が悪化するトレードオフが確認された。

### 2. volume追加で改善するか

**部分的。** G_B+O2 は retention 26.3%（O1より+2.3pt）だが PnL +21,500（O1比 −95k）、lost_big 131（+16）。G_B+O3 は retention 34.3% だが reintroduced MFE0=47 で MFE0品質が崩壊。volume単独追加は G_B 本命では O1 を上回れない。

### 3. day_leader追加で改善するか

**いいえ（G_B）。** G_B+O5/O7 は O1 より PnL・lost_big とも劣化。G_A+O7 は retention 50% 超だが PnL −7,650。

### 4. open_strength追加で改善するか

**いいえ（G_B）。** G_B+O6 は PnL +58,800（O1の半分以下）、lost_big 144。proxy は機能するが winner 回収力が不足。

### 5. retention 30%以上を達成できる候補はあるか

**G_B では不可。** 達成は G_A 全般（O1で53.7%）、G_C+O3（31.9%）、G_B+O3（34.3%、ただし reintro MFE0 47 で失格）。

### 6. lost_big_winner 100未満を達成できる候補はあるか

**G_B では不可（最低115=O1）。** G_A+O1=91、G_A+O3=93 のみ。いずれも G_B より guard が緩く PnL/PF が劣る。

### 7. MFE0再混入20件以下を維持できるか

**O3 系を除き維持可能。** 全30戦略中 reintroduced MFE0 ≤20 は 27/30。例外: G_A+O3(25)、G_B+O3(47)、G_C+O3(58)。

### 8. G_Bより改善したか

**いいえ。** priority score（PnL/PF/DD/MFE0/reintro/lost_big/retention 加重）で G_B+O1 が最高（80pt）。他 G_B+O* はすべて劣化。

### 9. Runtime候補になったか

**いいえ。** retention≥30% AND lost_big≤90 AND reintro MFE0≤20 を同時満たす候補は **0件**。Runtime変更・採用は禁止のまま。

### 10. Shadowへ進めるべき最終候補

**G_B+O1（board ≥ 0.60）を維持。** Phase543C の `forward_shadow_candidate` と一致。volume/day_leader 系は shadow 観測用の補助シグナルとして記録し、本番 override には採用しない。

## 依存性監査（G_B+O1）

| 指標 | 値 |
|------|-----|
| top1 symbol exclusion net | 要 `phase543d_override_tuning_dependency.csv` 参照 |
| top3 symbol / day | 同上 |
| top10 trade exclusion | 同上 |

O2/O3 は top3 依存が O1 より高くなる傾向（volume 集中銘柄への偏り）。

## 結論

- **board≥0.60 単独（O1）が G_B 本命の最適解** — volume/day_leader 追加は winner 回収と MFE0 品質のトレードオフで net negative。
- retention 30% / lost_big 90 の **両立は本データでは未達**。達成には guard 緩和（G_A）か volume 緩和（O3）が必要だが、いずれも reintroduced MFE0 または PnL で失格。
- **次ステップ:** G_B+O1 を shadow 継続。volume_surge クラスタは override ではなく **別レイヤ（entry filter 研究）** で扱う。

## 成果物

```
results/reports/phase543d_override_tuning_summary.csv
results/reports/phase543d_override_tuning_detail.csv
results/reports/phase543d_override_tuning_dependency.csv
results/reports/phase543d_report.json
docs/phase543d_guard_override_final_tuning.md
docs/operations/phase543d_guard_override_final_tuning.md
```

## 実行

```bash
python scripts/run_phase543d_guard_override_final_tuning.py --parallel --max-workers 4
```
