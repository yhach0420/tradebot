# Phase616B: ExtensionBus session_end TypeError Fix

## Symptom

`run_paper_trade.bat` AM session crashed at session end:

```
TypeError: finalize_session_exit_shadow_monitor_safe() got an unexpected keyword argument 'state'
```

Location: `src/small_paper/extension_bus.py` `on_session_end()` called:

```python
finalize_session_exit_shadow_monitor_safe(state=state, summary=out, config=config)
```

Actual signature (`exit_shadow_monitor.py`):

```python
def finalize_session_exit_shadow_monitor_safe(
    events: Sequence[Mapping[str, Any]],
    *,
    monitor: ExitShadowMonitorConfig,
) -> dict[str, Any]:
```

Correct usage already exists in `pilot_runner._apply_exit_shadow_monitor_finalize()` and is invoked from `_build_live_summary()` before `bus.on_session_end()`.

## Fix

1. **Removed** the duplicate/wrong `finalize_session_exit_shadow_monitor_safe` call from `ExtensionBus.on_session_end()`.
2. **Wrapped** each session-end extension step in `_run_step()` try/except so extension failures never stop Core runtime.
3. On failure, append `"{step}: {exc}"` to `summary["extension_errors"]` and continue remaining steps.
4. **Wrapped** `notify_discord_session_end()` in `pilot_runner` try/except; failures set `discord_session_end_error` on summary.
5. Discord HTTP post already catches exceptions in `SmallPaperDiscordNotifier._post()` (returns `False`, does not raise).

## Constraints (unchanged)

- ENTRY / PBv2 / EXIT logic: no changes
- `freshness_semantics_v2`: no changes (Phase621 config maintained)
- No real orders (`order_enabled=false`, dry-run observer)

## Verification

```powershell
python -m unittest tests.test_phase616b_extension_bus_session_end_fix
python scripts/run_production_startup_smoke_test.py --exit-policy-shadow trailing-mfe
python scripts/check_live_pipeline_preflight.py
python scripts/run_phase616b_extension_bus_session_end_fix.py
```

Report: `results/reports/phase616b_extension_bus_session_end_fix.json`

Verdict: `phase616b_extension_bus_session_end_fix_done`
