# Phase496 — MST Near High Optimization

**Verdict:** `needs_forward_shadow`  
**Period:** 20260529 — 20260622 | Baseline: PnL +244,963 / PF 1.62

## スコア定義

| 名称 | 定義 |
|------|------|
| `distance_from_day_high_pct` | `day_high_distance_pct`（小さいほど高値圏） |
| `MST_near_day_high_score` | `1 / max(distance, 0.05)`（大きいほど高値圏） |
| Gate | `distance <= threshold` で reject（pool 下位 N% をブロック） |

## 必須回答

| 質問 | 回答 |
|------|------|
| 最良閾値 | **reject 30% → dhd ≤ 1.977** (score ≥ 0.506) |
| 最良 delta | **+119,401** (364,364 vs 244,963) |
| 最良 PF | **2.76** (baseline 1.62) |
| blocked winners | **57** |
| blocked losers | **50** |
| runtime候補か | **No** — winner FP 57件 |
| shadow候補か | **Yes** — 5〜15% 帯を forward-shadow |
| hard rejectより改善したか | **Yes (delta)** — +119k vs hard +47k (+107k Phase495) |

### 実用帯（winner FP 制約）

| reject% | threshold | delta PnL | PF | blocked W/L |
|---------|-----------|-----------|-----|-------------|
| **5%** | ≤0.33 | +39,501 | 1.78 | **12/9** |
| 10% | ≤0.70 | +34,901 | 1.83 | 23/15 |
| 15% | ≤1.11 | +65,102 | 2.07 | 31/26 |
| 20% | ≤1.44 | +66,101 | 2.20 | 42/33 |
| hard <1.0 | ≤1.00 | +46,901 | 1.95 | 29/23 |
| **30%** | ≤1.98 | **+119,401** | **2.76** | 57/50 |

**推奨 Shadow 閾値:** reject **5〜10%**（dhd ≤ 0.33〜0.70）— delta +35k〜+40k、blocked winners 12〜23。

## Grid 全結果

| reject% | threshold | delta | PF | maxDD | W/L blocked | 6976 | 4062 | AM | PM |
|---------|-----------|-------|-----|-------|-------------|------|------|-----|-----|
| 30% | 1.977 | +119,401 | 2.76 | 24,700 | 57/50 | +10k | -12k | -57k | +14k |
| 25% | 1.751 | +109,801 | 2.57 | 30,399 | 49/41 | +9k | -12k | -54k | +14k |
| 20% | 1.437 | +66,101 | 2.20 | 34,399 | 42/33 | +9k | -12k | -34k | +14k |
| 15% | 1.105 | +65,102 | 2.07 | 36,500 | 31/26 | +1k | -12k | -22k | +10k |
| 5% | 0.330 | +39,501 | 1.78 | 40,399 | 12/9 | +1k | -4k | +7k | +1k |
| 10% | 0.704 | +34,901 | 1.83 | 40,199 | 23/15 | +1k | -4k | -4k | +12k |
| hard<1.0 | 1.000 | +46,901 | 1.95 | 36,500 | 29/23 | +1k | +1k | -8k | +10k |
| 40% | 2.640 | -54,269 | 1.82 | 87,500 | 80/59 | +4k | -5k | -55k | +54k |
| 50% | 3.488 | -226,869 | 1.09 | 102,300 | 100/68 | +104k | -9k | +75k | +48k |

40%以上は明確に悪化 → **reject 上限 30% 以下**。

## Robustness（最良 30%）

| Test | delta vs baseline |
|------|-------------------|
| LOO 13日 | 全て **+70k〜+125k** |
| exclude_6976 | +129,402 |
| exclude_4062 | +107,899 |
| exclude_top_day (6/11) | +101,302 |

LOO は安定だが winner FP が大きいため **overfit_threshold ではなく needs_forward_shadow**。

## 判定理由

- delta 最大化（30%）は hard reject より **+72k 改善** だが blocked winners 57 → Runtime 不可
- 5% soft gate は hard reject と比べ delta はやや劣るが **winner FP 12** と大幅改善
- AM impact は広い閾値で常にマイナス、PM はプラス → **PM 損失除去型 guard**

## 次アクション

1. Forward-shadow: log `distance_from_day_high_pct` + would_reject_5pct / would_reject_10pct
2. Live で 5% 閾値（dhd≤0.33）の FP 率を 2週間観測
3. Runtime 投入は blocked winners ≤5 かつ delta > +20k を満たす閾値が見つかってから

## 成果物

- `results/reports/phase496_mst_threshold_grid.csv`
- `results/reports/phase496_mst_robustness.csv`
- `results/reports/phase496_summary.json`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase496_mst_near_high_optimization.py --parallel --max-workers 2
```
