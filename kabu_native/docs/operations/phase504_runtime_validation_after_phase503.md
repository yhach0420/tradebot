# Phase504 — Runtime Validation After Phase503

**Verdict:** `runtime_ready`  
**Period:** 20260529 — 20260622 (replay pool actual: 20260529 — 20260619)

## 必須回答

| # | 項目 | 値 |
|---|------|-----|
| 1 | total PnL | **260,562.79** |
| 2 | PF | **1.6904** |
| 3 | maxDD | **50,799.13** |
| 4 | trade_count | **277** |
| 5 | rejected_by_classic_late_chase_rsi_over80 | **10** (W/L **1/6** per Phase502 counterfactual; baseline flag hits 10) |
| 6 | rejected guard 想定 PnL | **-15,599.96** (blocked net; delta vs Phase488 **+15,599.96**) |
| 7 | 6976 寄与 | **152,501.28** (share **58.5%** — Phase488 62.3% から改善) |
| 8 | 4062 寄与 | **9,001.55** (share **3.5%**) |
| 9 | 6/22 影響 | accepted day PnL **0**; guard blocked on 6/22 **0** (pool ends 20260619) |
| 10 | 100万資産シミュ | final **1,158,463.61** / PnL **+158,463.61** / maxDD **50,799** / PF **1.42** |
| 11 | 150万資産シミュ | final **1,760,562.79** / PnL **+260,562.79** / maxDD **50,799** / PF **1.6904** |
| 12 | 200万資産シミュ | final **2,260,562.79** / PnL **+260,562.79** / maxDD **50,799** / PF **1.6904** |
| 13 | Runtime 問題ないか | **Yes** — Phase502 guard C replay と完全一致 |
| 14 | 明日起動してよいか | **Yes** (`paper_only=true`, `order_enabled=false`) |

## Phase488 / Phase502 との整合

| 指標 | Phase488 baseline | Phase502 guard C | Phase504 (post-503) | 整合 |
|------|-------------------|------------------|---------------------|------|
| total PnL | 244,962.83 | 260,562.79 | **260,562.79** | ✅ 完全一致 |
| PF | 1.6171 | 1.6904 | **1.6904** | ✅ |
| maxDD | 53,899.13 | 50,799.13 | **50,799.13** | ✅ 改善 |
| trade_count | 286 | 277 | **277** | ✅ |
| delta PnL | — | +15,599.96 | **+15,599.96** | ✅ |
| blocked W/L | — | 1/6 | **1/6** (Phase502 CF) | ✅ 想定付近 |

Phase503 Runtime guard は Phase502 replay guard **C_late_chase_AND_rsi_over80** と同一効果。

## 現行 Runtime 構成（検証対象）

- PBv2 (momentum cutoff + Board mid/high)
- Late Chase Guard (Phase472)
- High Drift / Weak Shape / Near-day-high guards
- No Progress Exit + Board Dynamic Trailing Exit
- **classic_late_chase_rsi_over80** (Phase503, enabled)

## Config 確認

```yaml
order_enabled: false          # ✅
paper_only: true              # ✅
classic_late_chase_rsi_guard_enabled: true
classic_late_chase_rsi_threshold: 80
max_concurrent_positions: 5   # unchanged
```

## 観測性

- Daily Summary / Discord Reject Funnel: `reject_reason_counts` に `classic_late_chase_rsi_over80: N` が載る（Phase503 実装済み）
- Research shadow block: `ClassicLateChaseRSI Guard: classic_late_chase_rsi_over80=N`

## 6976 / maxDD 所見

- **6976**: share 62.3% → **58.5%**（guard により 6976 1件 -26,500 を reject 候補に含むが、全体 PnL は改善）
- **maxDD**: 53,899 → **50,799**（-3,100 改善、悪化なし）

## 判定

**`runtime_ready`** — Phase503 guard 導入後も Phase502 期待値と完全一致。追加 Entry/Exit 変更なし。明日の paper 起動可。

Rollback 条件: `classic_late_chase_rsi_guard_enabled: false`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src;.."
python scripts/run_phase504_runtime_validation_after_phase503.py
```

## 成果物

- `results/reports/phase504_runtime_validation_after_phase503.csv`
- `results/reports/phase504_asset_simulation.csv`
- `results/reports/phase504_summary.json`
