# Phase630: Runtime Parity CI

## Purpose

Automatically detect unintended ENTRY/EXIT behavior changes after refactors or
feature work, using the Phase629A parity method (same PUSH fixtures, same
production YAML, shared vol_liq cache key, market-time scan clock).

## Manual command

```bash
python scripts/check_runtime_parity.py
```

Options:

| Flag | Meaning |
|---|---|
| `--days 2026-06-25 ...` | Subset of fixture days (default: 6/25, 6/29, 6/30, 7/1) |
| `--baseline-root PATH` | Source tree for HEAD_BASELINE (default: current repo) |
| `--current-root PATH` | Source tree for CURRENT (default: current repo) |
| `--reuse` | Reuse existing `_phase630/head_baseline` and `current` outputs |
| `--no-volatile` | Do **not** exclude volatile fields (expect wall-clock noise) |

Exit codes: `0` = ALL_MATCH, `1` = mismatch or setup error.

## What is compared

From `small_paper_summary.json` (no full events.jsonl load):

- candidates (`candidate_count`)
- gate_evaluations
- PBv2 accepted (`pbv2_count`)
- OR accepted (`or_count` / `or_entry_count`)
- accepted total (`accepted_count`)
- exits (`observer_exit_count_with_pnl`)
- pnl_yen_100 (`total_pnl_yen_100`)
- reject reason counts (`reject_reason_counts`)
- remaining summary metrics except the volatile whitelist
- positions.csv row count

## Volatile whitelist

See `results/reports/phase630_runtime_parity_ci/phase630_volatile_whitelist.csv`.

Categories:

- wall_clock / runtime timestamps and latency percentiles
- run_id / output_dir / session key
- vol_liq cache metadata (hit/path/timing; thresholds stay identical via shared staging key)

## Artifacts

`results/reports/phase630_runtime_parity_ci/`

- `phase630_parity_summary.json`
- `phase630_parity_by_day.csv`
- `phase630_diff_if_failed.csv.gz` (header-only when pass; compact diffs when fail)
- `phase630_volatile_whitelist.csv`
- `phase630_report.json`

Replay outputs (large): `results/small_paper/_phase630/{head_baseline,current,staging}/`

## Preflight

Optional. Do **not** wire into `run_paper_trade.bat` until a few manual green runs
are confirmed. When ready:

```bat
python scripts\check_runtime_parity.py --reuse
```

Use `--reuse` only when outputs are known fresh; otherwise omit it for a full check.

## Rollback

Delete:

- `scripts/check_runtime_parity.py`
- `docs/operations/phase630_runtime_parity_ci.md`
- `results/reports/phase630_runtime_parity_ci/`
- `results/small_paper/_phase630/`

No ENTRY/EXIT/PBv2/OR/Freshness/YAML logic is modified by Phase630.
