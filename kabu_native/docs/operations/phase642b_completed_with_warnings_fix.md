# Phase642b: completed_with_warnings Policy Bug Fix

## Incident (20260706)

AM session `live_session_080937` completed normally (`stop_reason=session_end`,
`small_paper_summary.json` written) but daily runner verdict was `am_failed` → PM skipped.

**Root cause:** `UnicodeEncodeError(cp932)` in `run_small_paper_pilot.py` when printing
summary JSON containing em-dash (`\u2014` in `comparison_note`) **after** session end.

Phase642 only accepted `stop_reason=completed`, not `session_end`.

## Fixes

1. **`PILOT_SOFT_OK_STOP_REASONS`**: `completed`, `session_end`, `morning_session_close`, `afternoon_session_close`
2. **`is_post_session_subprocess_failure()`**: stderr traceback in pilot `main()` print path
3. **`run_small_paper_pilot.py`**: `stdout.reconfigure(utf-8, errors=replace)` + safe print → exit 0

## Verdict order

1. subprocess `exit_code`
2. `_pilot_completed_with_warnings` (summary finalized + soft stop_reason OR post-session print fail)
3. `completed_with_warnings` if soft_ok else `failed`
4. `_pilot_failed_hard` → blocks PM only on true failure

## Rollback

Revert `am_pm_daily_runner.py` / `pilot_subprocess_logging.py` / `run_small_paper_pilot.py` changes.

## Test

```bash
python -m pytest tests/test_phase642b_completed_with_warnings_fix.py tests/test_phase642_daily_runner_verdict_policy.py -q
python scripts/run_phase642b_completed_with_warnings_fix.py
```

## Verdict

`phase642b_completed_with_warnings_fix_done`
