# Phase613 — Runtime Latency / Freshness Timeout Audit (Disk-Safe Parallel)

**Verdict:** `phase613_disk_safe_parallel_latency_audit_done`

## P0 Disk recovery

| Item | Value |
|------|-------|
| Prior cleanup (2026-06-30 19:03) | ~75.5 GB freed (`disk_cleanup_result_20260630_1903.csv`) |
| This run cleanup (21:29) | 0 GB (no deletable artifacts; already clean) |
| Free space now | **139.2 GB** (target ≥50 GB met) |

Plans/results: `results/reports/disk_cleanup_plan_*.csv`, `disk_cleanup_result_*.csv`

## Run

```powershell
$env:PYTHONPATH="src;.."
python scripts/run_phase613_disk_safe_parallel_latency_audit.py
```

4 parallel jobs (`max_workers=4`):

| Job | Session |
|-----|---------|
| A | 6/25 GOOD (AM+PM) |
| B | 6/29 AM BAD |
| C | 6/29 PM BAD |
| D | 6/30 AM BAD |

Outputs: `results/reports/phase613_parallel/` (gzip samples only, no raw payload).

## Live measurement (next session)

```powershell
$env:ENTRY_LATENCY_TRACE_ENABLED="true"
# or entry_latency_trace_enabled: true in YAML
```

Writes `entry_latency_trace.jsonl` per session (all `data_stale_price` + 0.2% pass sample) with full t0–t6 stage breakdown.

## Stale classification

| Class | Rule |
|-------|------|
| A_feed_already_stale | `d_feed_price_age_at_push > entry_max_price_age_sec` (3s) |
| B_system_latency_stale | feed fresh at push, stale at freshness check |
| C_missing_current_price_time | CurrentPriceTime missing |
| D_parse_or_timezone_error | parse failure / future timestamp |
| E_other | remainder |

Historical reconstruction: `push_jsonl.recorded_at` → `entry_scan_audit.eval_start_ts` for system delay; audit `price_age_sec` / `board_age_sec` at freshness.

## Mandatory answers (aggregate, 134,554 `data_stale_price` rows)

1. **Disk freed:** prior ~75.5 GB; this run 0 GB; **139 GB free** now.
2. **Primary cause:** **A_feed_already_stale** (106,960 vs B 25,442).
3. **Push → freshness median:** **2,168 ms** (`d_system_to_freshness_ms`).
4. **Over 3s system latency:** **51,486** candidates.
5. **Slowest stage:** **d_system_to_freshness_ms** (push receive → freshness check). Per-symbol eval (`d_total_pipeline_ms`) median ~0.9 ms.
6. **625 vs 629/630 latency:** GOOD median sys 1,730 ms / feed age 7.7 s; BAD 2,580 ms / feed age 10.8 s. BAD worse but both cohorts show multi-second queue delay.
7. **Heavy modules (vol_liq, live_order):** ON on 629/630, OFF on 625. Eval latency ~1 ms unchanged — modules run post-accept, not in freshness hot path. Correlates with session config, not sole stale cause.
8. **CurrentPriceTime age at push (median):** **9.78 s** (often already stale before freshness).
9. **Board fresh, price stale:** **131,699** rows.
10. **System creates stale?** **Yes (partial)** — 25,442 B_system_latency_stale; dominant driver remains feed staleness (A).
11. **Structural fixes:** F1 `latest_trade_or_board_ts` anchor; F2 conditional `board_fallback`; F3 persist eval-time payload; reduce scan-batch queue (`poll_interval_sec=5`); enable `entry_latency_trace` for live t0–t6.

## Key files

- `results/reports/phase613_parallel/phase613_report.json`
- `results/reports/phase613_parallel/phase613_stale_classification_summary.csv`
- `results/reports/phase613_parallel/phase613_latency_bucket_summary.csv`
- `results/reports/phase613_parallel/job_*/summary.json`
- `results/reports/phase613_parallel/job_*/*.csv.gz` (samples)
