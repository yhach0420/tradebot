# Phase411 — Phase409 Fix + Same-Symbol Reentry Shadow

Generated: 2026-06-16

## Summary

Phase410 で特定した Phase409 不発（6/16 `day_count=0` / `trade_count=0`）を修正し、
`same_symbol_open_reentry_reject` の forward shadow（Phase411）を research 専用で開始した。

**Runtime / YAML / Entry / Exit / Discord / Order 本線は一切変更していない。**

## Part A — Phase409 修正

### 修正内容

| 項目 | 修正 |
|------|------|
| `close_time` → `exit_time` | `structural_trade_normalize.normalize_structural_trade_row()` でマップ |
| `open_time` / `timestamp` → `entry_time` | 欠損時に補完 |
| 列名揺れ | `pnl_yen_100_raw`, `close_reason`, symbol 正規化 (`_norm_symbol`) |
| パス解決 | `resolve_kabu_root()` — `kabu_native/kabu_native` 二重パス回避 |
| dedupe | `(day, session, symbol, entry_time)` — 同一時刻の別銘柄を落とさない |
| `day_count` | `daily_rows` ベース。structural_trades 存在日は trade=0 でも加算 |

### 6/16 再実行結果（修正後）

| 指標 | 値 | 期待 | 判定 |
|------|-----|------|------|
| `day_count` | 1 | 1 | PASS |
| `session_count` | 2 | ≥1 | PASS |
| `trade_count` | 774 | >0 | PASS |
| `boundary_eligible_count` | 37 | ≈37 | PASS |
| `affected_trade_count` | 33 | ≈33 boundary影響 | PASS |
| `boundary_exit_count` | 0 | — | 短保有のため shadow boundary 発火は限定的 |
| `post_baseline_usage_count` | 0 | 0 | PASS |
| `replay_audit_pass` | true | true | PASS |

出力:

- `results/reports/phase409_boundary_forward_shadow_summary.json`
- `results/reports/phase409_boundary_forward_shadow_trades.csv`
- `results/reports/phase409_boundary_forward_shadow_daily.csv`
- `results/daily/20260616/research/` にコピー

## Part B — Same-Symbol Reentry Shadow

### Policy

`same_symbol_open_reentry_reject`: 同一銘柄が open 中の再 ENTRY を shadow で reject。
既存ポジションは維持。`overlap_replaced_review` 連鎖を発生させない。

### 6/16 結果（Phase410 counterfactual 一致確認）

| 指標 | Phase411 | Phase410 期待 | 判定 |
|------|----------|---------------|------|
| baseline trades | 774 | 774 | PASS |
| shadow trades | 394 | ≈394 | PASS |
| shadow PnL | +23,200 | ≈+23,200 | PASS |
| shadow PF | 1.169 | ≈1.17 | PASS |
| shadow maxDD | 32,300 | ≈32,300 | PASS |
| reject count | 380 | — | — |
| overlap 削減 | 368 | — | — |

出力:

- `results/reports/phase411_same_symbol_reentry_shadow_trades.csv`
- `results/reports/phase411_same_symbol_reentry_shadow_daily.csv`
- `results/reports/phase411_same_symbol_reentry_shadow_summary.json`
- `results/daily/20260616/research/` にコピー

### 採用ゲート

- `day_count < 5`: **observe**（現状）
- `day_count >= 5`: review_required
- `day_count >= 10`: adoption_review_allowed
- **自動採用禁止**

## Part C — Phase409 × Same-Symbol Interaction

| 指標 | baseline | same_symbol shadow | 変化 |
|------|----------|-------------------|------|
| `boundary_eligible_count` | 37 | 27 | −10（短保有トレード削減） |
| `boundary_exit_count` | 0 | 0 | 変化なし |

同一銘柄再 ENTRY 拒否により churn は大幅削減（774→394）するが、
6/16 時点では median hold 15秒のため Phase409 boundary shadow の実発火は依然限定的。
保有時間が伸びたトレード集合では eligible が残存（27件）。

## 変更ファイル

- `src/research/structural_trade_normalize.py`（新規）
- `src/research/phase409_boundary_forward_shadow.py`
- `src/research/phase411_same_symbol_reentry_shadow.py`（新規）
- `src/small_paper/boundary_forward_shadow_auto.py`
- `scripts/run_phase409_boundary_forward_shadow.py`
- `scripts/run_phase411_same_symbol_reentry_shadow.py`（新規）
- `src/storage/results_paths.py`（`phase411_` prefix）
- `tests/test_phase409_boundary_forward_shadow.py`
- `tests/test_phase411_same_symbol_reentry_shadow.py`（新規）

## 次ステップ（Runtime 反映は別 Phase）

1. forward shadow を 5 営業日以上継続観測
2. `same_symbol_open_reentry_reject` の採用レビュー（手動のみ）
3. boundary eligible が増える日での Phase409 再評価
