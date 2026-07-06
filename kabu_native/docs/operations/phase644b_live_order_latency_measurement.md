# Phase644b: Live Paper Order Latency Measurement

## Purpose

Aggregate **live paper** `order_latency_dryrun_trace.jsonl` files produced during Phase644-enabled sessions.
No real orders; `order_enabled=false` unchanged.

## Data source

```
results/small_paper/<YYYYMMDD>/live_session_*/order_latency_dryrun_trace.jsonl
```

Excluded:
- `push-replay` sessions
- `_phase630`, `_synthetic_probe`, `reports/` paths

## Run

```bash
python scripts/run_phase644b_live_order_latency_measurement.py
python -m pytest tests/test_phase644b_live_order_latency_measurement.py -q
```

## Metrics

| Metric | Definition |
|--------|------------|
| `price_to_order_sec` | t9 − t0 (CurrentPriceTime) |
| `push_to_order_sec` | t9 − t1 (recorded_at) |
| `push_to_decision_ms` | t5 − t1 |
| `decision_to_order_ms` | t9 − t5 |
| `queue_latency_ms` | t7 − t6 |
| `order_build_ms` | t8 − t7 |
| `dryrun_ms` | t10 − t9 |

Statistics: p50, p90, p95, p99, max, mean

## Thresholds

| Check | Threshold |
|-------|-----------|
| push→order p95 | ≤ 1.5s pass |
| push→order p99 | ≤ 3.0s (warning above) |
| price→order p95 | ≤ 2.0s pass |
| queue_latency p95 | > 500ms warning |
| decision_latency p95 | > 500ms warning |

## Discord Summary

```
[Order Latency DryRun]
samples: N
push→order p50/p95/p99/max: …
price→order p50/p95/p99/max: …
top bottleneck: …
alert: …
```

## Artifacts

```
results/reports/phase644b_live_order_latency/
  phase644b_report.json
  phase644b_latency_summary.csv
  phase644b_by_pool.csv
  phase644b_by_symbol.csv
  phase644b_by_timebucket.csv
  phase644b_latency_top20.csv
```

## Current status

As of report generation, **no live session traces** may exist yet if paper has not run since Phase644 wiring.
Re-run this script after the next AM/PM paper session.

## Verdict

`phase644b_live_order_latency_measurement_done`
