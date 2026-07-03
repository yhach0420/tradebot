# Phase612A — Pre625 Runtime Structure Mode

**Verdict:** `phase612a_pre625_runtime_structure_mode`

HEAD code unchanged; runtime wiring reverted to pre-6/25 equivalent.

## Enable

- Env: `PRE625_RUNTIME_STRUCTURE_MODE=true`
- CLI: `--pre625-runtime-structure-mode` on pilot / daily runner
- Batch: `run_paper_trade_pre625_structure.bat` (does not modify `run_paper_trade.bat`)

## Forced OFF when mode active

| Flag | Effect |
|------|--------|
| `live_order_adapter_enabled` | No adapter session |
| `live_order_notifier_enabled` | No notifier JSONL |
| `live_capital_check_enabled` | No capital check hooks |
| `entry_freshness_board_fallback_enabled` | Pre-625 freshness path |
| `vol_liq_startup_cache_enabled` | No startup cache |
| `live_order_dry_run_enabled` | Legacy dry-run hooks off |
| `live_order_api_wiring_enabled` | Wiring hooks off |
| `live_order_jsonl_enabled` | Heavy order JSONL off |
| `volume_gate_relaxation_shadow_enabled` | Shadow eval off |

## Persistence

`live_session_config.json`:
- `pre625_runtime_structure_mode`: true/false
- `pre625_runtime_structure_forced_off`: dict of OFF flags

Startup log: `[PAPER TRADE] pre625_runtime_structure_mode=true`

## Verify

```bash
PYTHONPATH=src;.. python -m pytest tests/test_phase612a_pre625_runtime_structure_mode.py -q
PYTHONPATH=src;.. python scripts/run_phase612a_pre625_runtime_structure_mode_audit.py \
  --head-session results/small_paper/20260629/... \
  --pre625-session results/small_paper/20260630/...
```

ENTRY/EXIT gate logic on HEAD is not modified.
