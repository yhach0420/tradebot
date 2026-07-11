# Phase653: AM/PM Session Summary Preservation

Storage-only change: preserve immutable AM/PM snapshots without altering ENTRY/EXIT logic or YAML.

## Behavior

| Event | Action |
|-------|--------|
| AM live session end | Copy `small_paper_summary.json` → `small_paper_summary_am.json` (same `live_session_*` dir) |
| PM live session end | Copy → `small_paper_summary_pm.json` |
| daily_runner `write_outputs` | Mirror to `kabu_native/results/reports/daily_runner/daily_summary_am_YYYYMMDD.json` and `daily_summary_pm_YYYYMMDD.json` |
| `daily_runner_summary_YYYYMMDD.json` | Adds `am_summary_path`, `pm_summary_path` |

`small_paper_summary.json` is **unchanged** (still written as today).

## Code

- `src/small_paper/am_pm_summary_preservation.py`
- Hook: `pilot_runner.run_live_dry_run` (after `finalize_batch`)
- Hook: `runner.am_pm_daily_runner.write_outputs`

## Run verification

```bash
python -m pytest tests/test_phase653_am_pm_summary_preservation.py -q
python scripts/run_phase653_am_pm_summary_preservation.py
```

## Artifacts

```
results/reports/phase653_am_pm_summary_preservation/phase653_report.json
kabu_native/results/reports/daily_runner/daily_summary_am_YYYYMMDD.json
kabu_native/results/reports/daily_runner/daily_summary_pm_YYYYMMDD.json
```

## Verdict

`phase653_am_pm_summary_preservation_done`
