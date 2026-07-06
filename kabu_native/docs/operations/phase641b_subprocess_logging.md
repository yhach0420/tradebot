# Phase641b: Pilot Subprocess Logging Enhancement

## Problem

Phase641 found **20260701** returned `exit_code=1` while the session summary was complete.
Phase642 introduced `completed_with_warnings`, but only a short stderr tail was retained —
insufficient for root-cause analysis.

## Solution

Orchestration-only changes in the daily runner (no ENTRY/EXIT/PBv2/YAML/runtime trading logic changes).

### Subprocess capture

`run_pilot_session` captures **stdout and stderr** via `subprocess.run(capture_output=True)`.

### Session artifacts

Per session directory under `results/small_paper/<date>/live_session_xxxxxx/`:

| File | Content |
|------|---------|
| `pilot_stdout.log` | Full stdout |
| `pilot_stderr.log` | Full stderr (includes proc_error if subprocess launch failed) |

### Daily runner summary fields

`build_summary_payload` adds (AM/PM prefixed):

- `*_pilot_exit_code`
- `*_pilot_stdout_path`
- `*_pilot_stderr_path`
- `*_stdout_last_20_lines` (tail only in JSON summary)
- `*_stderr_last_20_lines`
- `*_first_exception`, `*_first_traceback`, `*_first_error_line` (when parseable)

### Verdict integration

| Verdict | Logging behavior |
|---------|------------------|
| `completed_with_warnings` | `warning_notes` include stderr/stdout summaries (last lines) |
| `am_failed` / `pm_failed` | `state.sessions[am_pilot_failure]` stores traceback diagnostics |

### Discord System Health

`format_runtime_health_lines` adds:

```
Pilot Exit: 0
Pilot Exit: 1 (warning)
Pilot Exit: 1 (failed)
```

Values come from `pilot_exit_display` on `small_paper_summary.json` (patched post-pilot) or computed from exit code + verdict.

## Module

`src/runner/pilot_subprocess_logging.py` — persist, tail, traceback parse, warning notes, Discord display helper.

## Tests

```bash
python -m pytest tests/test_phase641b_subprocess_logging.py tests/test_phase642_daily_runner_verdict_policy.py tests/test_am_pm_daily_runner_session_dirs.py -q
python scripts/run_phase641b_subprocess_logging.py
```

Scenarios covered:

- exit 0, stdout only
- exit 1 warning with stderr
- exit 1 crash with traceback
- huge stdout/stderr (full files, 20-line summary tail)
- accepted/parity unchanged (no pilot_runner trading logic touched)

## Artifacts

- `results/reports/phase641b_subprocess_logging/phase641b_report.json`

## Verdict

`phase641b_subprocess_logging_done`
