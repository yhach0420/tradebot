# Phase644: Live Order Latency Readiness Audit

## Purpose

Measure time from kabu PUSH price recognition (`CurrentPriceTime`) through sendorder **dry-run**
(no real orders). Supports live-order readiness assessment.

## Measurement points

| Mark | Meaning |
|------|---------|
| t0 | `CurrentPriceTime` (kabu PUSH) |
| t1 | WebSocket `recorded_at` |
| t2 | `_process_push_payload` start |
| t3 | `enrich_payload` end |
| t4 | Freshness end |
| t5 | PBv2/OR decision end |
| t6 | Accepted queue enqueue |
| t7 | Accepted queue flush start |
| t8 | Order payload build end |
| t9 | Sendorder dry-run start |
| t10 | Sendorder dry-run end |

## Sample kinds

- `pbv2_accepted` / `or_accepted` — reached sendorder dry-run
- `cap_blocked` — `max_concurrent` reject
- `max_scan_blocked` — batch scan cap reject

## Runtime artifact

Per live session:

```
results/small_paper/<date>/live_session_*/order_latency_dryrun_trace.jsonl
```

Enabled when `order_latency_dryrun_trace_enabled=true` (default) and wiring dry-run active.

## Discord Summary

Observability embed adds:

```
[Order Latency DryRun]
p50 push→order: …
p95 push→order: …
max push→order: …
p50 price→order: …
p95 price→order: …
```

## Report

```bash
python -m pytest tests/test_phase644_live_order_latency_readiness.py -q
python scripts/run_phase644_live_order_latency_readiness.py
```

Outputs under `results/reports/phase644_live_order_latency/`:

- `phase644_report.json`
- `phase644_latency_summary.csv`
- `phase644_latency_samples.csv.gz`
- `phase644_by_pool.csv`
- `phase644_by_symbol.csv`

## Constraints

- No real orders (`order_enabled=false`, dry-run only)
- No ENTRY/EXIT/PBv2/OR logic changes
- No YAML threshold changes (new trace flag only)

## Verdict

`phase644_live_order_latency_readiness_done`
