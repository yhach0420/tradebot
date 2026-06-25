# Phase399 — Historical Position-CAP Backfill

Generated: 2026-06-25T07:12:14+09:00

## Verdict: **historical_backfill_ready**

## 重要な制限（必読）

1. **過去 Runtime とは別基準** — 旧 Runtime は 5 分 virtual-hold CAP、本 backfill は structural EXIT まで拘束する Position-CAP。
2. **旧 max_concurrent reject 候補は完全復元不可** — reject された候補には structural exit が無く、母集団は gate-accepted 実観測トレードに限定される。
3. **連続履歴としての用途** — 6/16 以降の新 Runtime（Position-CAP Mode）と比較可能な再計算系列として使用する（Runtime 採用判定ではない）。

### 再計算モデル

| モデル | 説明 |
|--------|------|
| **A. legacy_virtual_hold_runtime** | 旧 Runtime 相当（5分 VH CAP）。`small_paper_summary.json` の accepted 件数を参照。 |
| **B. position_cap_backfill** | 新 Runtime 相当。CAP=3、structural EXIT まで拘束。structural タイムラインで再評価（Phase395/396/397 一致）。 |
| **C. capital_shadow_1500k** | 1.5M / lev2 / 100株 / CAP3 / fixed_stop_1p2（Phase267–274 エンジン）。 |

### 明日以降の評価基準

- Live Runtime: `position_cap_mode=true` → observer open ≤3 until structural EXIT
- 本履歴: モデル B/C の session 集計を forward 比較のベースラインとする
- 150万円資産曲線: `capital_shadow_*` 列（モデル C）

### Period: `20260615` – `20260615`

### 集計サマリー

- legacy_total_trades: 165
- position_cap_total_trades: 64
- capital_shadow_total_trades: 64
- legacy_total_pnl_yen_100: ¥49903.82
- position_cap_total_pnl_yen_100: ¥700.0
- capital_shadow_final_equity: ¥1500700.0
- max_drawdown_pct: 0.0%
- days_below_50pct: 0

### 20260615 PM 一致確認 (`live_session_122531`)

| 指標 | 期待 | 実績 |
|------|------|------|
| position_cap trades | 22 | 22 |
| capital_shadow trades | 22 | 22 |
| capital_shadow PnL | ¥18700.0 | ¥18700.0 |
| accepted-stream position_cap (参考) | — | 11 |
| fixture_pass | — | `True` |

### Run stats

- processed_sessions: 2
- structural_backfilled: 0
- skipped_push_replay: 0
- skipped_debug: 0
- parallel / max_workers: `True` / `4`

### Daily totals

| day | sessions | legacy | position_cap | capital_shadow | position_cap PnL | capital_shadow PnL |
|-----|----------|--------|--------------|----------------|------------------|------------------|
| 20260615 | 2 | 165 | 64 | 64 | ¥700.0 | ¥700.0 |

### Artifacts

- `results/reports/phase399_historical_position_cap_backfill_trades.csv`
- `results/reports/phase399_historical_position_cap_backfill_daily.csv`
- `results/reports/phase399_historical_position_cap_backfill_summary.json`
