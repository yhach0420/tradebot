# Phase620: Freshness Semantics Full-Period Backtest

## v2 (2026-07-01) — Disk-safe 8-parallel

緊急対応: v1 がディスク圧迫・未完了のため停止。v2 で再実行。

### P0 ディスク復旧

```powershell
python scripts/run_phase620_freshness_backtest_v2.py --cleanup-only
```

- 削除計画: `results/reports/phase620_disk_cleanup_plan_YYYYMMDD_HHMM.csv`
- 削除結果: `results/reports/phase620_disk_cleanup_result_YYYYMMDD_HHMM.csv`
- 再開条件: 空き **≥50GB**（abort **<30GB**）

### P1 出力設計

| 許可 | 禁止 |
|------|------|
| summary json, variant/daily csv | candidate 全件 |
| trades.csv.gz (job) | reject 全件 |
| reject_sample.csv.gz (≤500) | raw payload, per-tick jsonl |
| job_summary.json | 巨大 checkpoint |

Job 出力: `results/reports/phase620_freshness_backtest_v2/jobs/<variant>/<day>/`

Replay temp: `results/small_paper/_phase620_v2_temp/`（job 完了後即削除）

### P2 Variant（v2 絞り込み）

| ID | 設計 |
|----|------|
| baseline | CPT>3s `data_stale_price` + board 3s |
| A | event 3s + board 3s + trade soft tag |
| B | event 3s + board 3s + liquidity guard (spread≤20bps) |
| C | event **5s** + board 3s + trade soft tag |
| D | event **2s** + board 3s + trade soft tag |
| P603_ref | board_fallback ON（集計比較用） |

event lag モデル: replay 同期評価のため `PHASE620_EVENT_LAG_SEC=2.17`（Phase613 中央値）

### P3 実行

```powershell
cd kabu_native
python scripts/run_phase620_freshness_backtest_v2.py --workers 8
python scripts/run_phase620_freshness_backtest_v2.py --aggregate-only  # 再集計のみ
python scripts/run_phase620_freshness_backtest_v2.py --smoke           # 6/29 baseline+A
```

### P4 成果物

`results/reports/phase620_freshness_backtest_v2/`

- `phase620_summary.json`
- `phase620_variant_comparison.csv`
- `phase620_daily_comparison.csv`
- `phase620_trade_source_analysis.csv`
- `phase620_stale_reason_comparison.csv`
- `phase620_risk_metrics.csv`
- `phase620_disk_usage_report.csv`

### 制約

- 本線 runtime 変更禁止（worker monkey-patch のみ）
- 実注文禁止
- backtest/replay のみ

### Verdict

`phase620_freshness_backtest_v2_done`

## v1（廃止）

`phase620_freshness_backtest/` + `_phase620_freshness_checkpoints/` は cleanup 対象。最終 summary json は参照用に残可。
