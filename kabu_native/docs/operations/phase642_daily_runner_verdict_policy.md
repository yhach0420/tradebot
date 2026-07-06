# Phase642: Daily Runner Verdict Policy Fix

## Problem

Phase641 identified **20260701 false `am_failed`**: pilot subprocess returned `exit_code=1`
while `small_paper_summary.json` was complete (`stop_reason=completed`, 43 accepted).

`_pilot_failed_hard` treated any nonzero exit as hard failure.

## Fix

`src/runner/am_pm_daily_runner.py`:

| Function | Role |
|----------|------|
| `_pilot_completed_with_warnings` | Detect soft-OK when summary proves completed session |
| `_apply_pilot_verdict_policy` | Set `pilot_verdict`, `warning_notes`, `pilot_ok` |
| `_pilot_failed_hard` | Returns False for `completed_with_warnings` |
| `_record_pilot_soft_ok_notes` | Append warning notes to daily runner verdict_notes |
| `run_pilot_session` | Captures stderr tail; applies verdict policy after discovery |

### `completed_with_warnings` criteria (all required)

- `small_paper_summary.json` exists under detected session dir
- `stop_reason == "completed"`
- Session started (`push_messages` / `gate_evaluations` / `runtime_sec` > 0)
- Summary finalized (`generated_at` + `ended_at` present)
- No `fatal_error`, `proc_error`, or `post_pilot_error`
- Pilot `exit_code != 0`

### Daily runner verdicts

| Outcome | Verdict |
|---------|---------|
| AM+PM OK, no warnings | `am_pm_daily_runner_ready` |
| Any session soft-OK with warnings | `completed_with_warnings` |
| True pilot crash | `am_failed` / `pm_failed` (unchanged) |
| Preflight fail | `preflight_blocked` (unchanged) |

`warning_notes` include `pilot_exit_code` and `stderr_tail` summary.

## Tests

```bash
python -m pytest tests/test_phase642_daily_runner_verdict_policy.py tests/test_am_pm_daily_runner_session_dirs.py -q
python scripts/run_phase642_daily_runner_verdict_policy.py
```

## Artifacts

- `results/reports/phase642_daily_runner_verdict_policy/phase642_report.json`

## Verdict

`phase642_daily_runner_verdict_policy_done`
