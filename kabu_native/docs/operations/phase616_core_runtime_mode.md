# Phase616 — CoreRuntimeMode Implementation

**Verdict:** `phase616_core_runtime_mode_done`

## Modes

| Mode | ExtensionBus | Audit JSONL | live_order / shadows |
|------|--------------|-------------|----------------------|
| `CORE_ONLY` | OFF | OFF | OFF |
| `CORE_PLUS_AUDIT` | ON (audit only) | ON | OFF |
| `FULL_EXTENSION` | ON (all) | ON | ON (YAML defaults) |

`pre625_runtime_structure_mode` → alias for `CORE_ONLY`.

## Core hot path (unchanged decision logic)

```
PUSH → enrich_payload → freshness → PBv2 → OR → ObserverPaperBook → LiveWriter
```

Implemented in `pilot_runner._process_push_payload`; Extension hooks via `ExtensionBus`.

## Modules

| File | Role |
|------|------|
| `src/small_paper/core_runtime_mode.py` | Mode enum, config apply, session fields |
| `src/small_paper/paper_trade_core.py` | Hot path documentation |
| `src/small_paper/extension_bus.py` | on_push_tick / on_post_eval / on_post_accept / on_session_end |
| `src/small_paper/pre625_runtime_structure_mode.py` | Delegates to CORE_ONLY |

## CLI

```powershell
# Full production (default)
python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live --core-runtime-mode FULL_EXTENSION

# Core only
python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live --core-runtime-mode CORE_ONLY

# Audit without live_order/shadows
python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live --core-runtime-mode CORE_PLUS_AUDIT
```

Environment: `CORE_RUNTIME_MODE=CORE_ONLY`

## Batch files

| File | Mode |
|------|------|
| `run_paper_trade.bat` | FULL_EXTENSION (default production) |
| `run_paper_trade_core_only.bat` | CORE_ONLY |
| `run_paper_trade_pre625_structure.bat` | CORE_ONLY (legacy alias) |

## Verification

```powershell
$env:PYTHONPATH="src;.."
python -m pytest tests/test_phase616_core_runtime_mode.py -q
python scripts/run_phase616_core_runtime_mode_audit.py
```

Outputs:
- `results/reports/phase616_core_runtime_mode_report.json`
- `results/reports/phase616_core_decision_parity.csv`
- `results/reports/phase616_core_vs_full_latency.csv`

## Design guarantees

1. ExtensionBus never modifies `GateDecision` / freshness outcome.
2. `CORE_ONLY` skips ExtensionBus init; paper trade runs on Core only.
3. PBv2 `evaluate_entry` path identical across modes (Phase610/615 confirmed).
