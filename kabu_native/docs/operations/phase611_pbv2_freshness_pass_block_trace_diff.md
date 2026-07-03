# Phase611 — Disk-safe PBv2 Freshness Pass/Block Trace Diff

**Verdict:** `phase611_disk_safe_parallel_trace_done`

Research-only per-candidate freshness trace with disk-safe sampling (no full dumps).

## P0 Disk recovery

| Metric | Value |
|--------|-------|
| Freed | **75.53 GB** |
| Free before | 80.43 GB |
| Free after | **155.96 GB** |

Deleted (68 items): `_phase603_backtest_checkpoints` (56.3 GB), `_phase600_replay` (19.0 GB), phase600–609 intermediate CSVs, phase611 huge CSVs.

Plans: `results/reports/disk_cleanup_plan_20260630_1903.csv`, `disk_cleanup_result_20260630_1903.csv`

Live sessions (`small_paper_events`, `rejects`, `summary`, final `*_report.json`) retained.

## P1–P3 Outputs

```
results/reports/phase611_parallel/
  phase611_report.json
  phase611_first_divergence_summary.csv.gz
  phase611_good_bad_diff_summary.csv.gz
  job_A_good625/  job_B_629am/  job_C_629pm/  job_D_630am/
    summary.json, *.csv.gz, log.txt
```

Limits: max 500 samples/bucket, 2000 bad/session, gzip, minimal columns, payload hash only.

## Mandatory answers

1. **Disk freed:** 75.53 GB (155.96 GB free now)
2. **Deleted:** phase checkpoints + research intermediate CSVs (see cleanup result CSV)
3. **6/25 PASS:** LIVE `price_age_sec≤3` all 70; `CurrentPriceTime` fresh path; 22/70 push-join stale but live audit fresh
4. **6/29–30 BLOCK:** 629 AM/PM `data_stale_price` dominant; 630 AM `or_overlay_not_candidate` dominant
5. **First diff variable:** `current_price_age_sec`
6. **Raw vs internal:** DATA/push-join gap, not parse/transform bug
7. **625-shape on 629/630:** YES — 98,889 / 100,767 score=3
8. **Where fell:** 52,699 stale at freshness; rest post-freshness guards / OR path
9. **Structural fixes:** F1 latest_trade_or_board_ts; F2 board_fallback; F3 audit payload persist
10. **Minimal rollback:** Conditional board_fallback (board≤3s + CalcPrice + spread≤50bps)

## Run

```bash
PYTHONPATH=src;.. python scripts/disk_cleanup_research_artifacts.py
PYTHONPATH=src;.. python scripts/run_phase611_disk_safe_parallel_trace.py
```
