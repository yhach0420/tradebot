# Phase615 — Core / Extension Runtime Separation Design

**Verdict:** `phase615_core_extension_runtime_separation_done`

## Purpose

Compare three runtime structures by responsibility unit and design full separation of **Paper Trade Core** vs **Extension Layer**.

| Variant | Description |
|---------|-------------|
| **pre625** | 6/25以前 (`f50c5a7`) — no vol_liq cache, no live_order stack |
| **head** | 現HEAD — full production extension wiring |
| **pre625_mode** | Phase612A — HEAD code, 9 extension flags forced OFF |

Phase610 confirmed: **PBv2 eval path order is identical** across all three; differences are which Extension hooks are active.

## Diagrams (draw.io)

Open in [diagrams.net](https://app.diagrams.net):

| File | Pages |
|------|-------|
| `results/reports/phase615_runtime_state_diagram.drawio` | State: pre625 / HEAD / 612A |
| `results/reports/phase615_runtime_flowchart.drawio` | Flow: PUSH → freshness → PBv2 → accept |
| `results/reports/phase615_runtime_sequence.drawio` | Sequence: Push, PilotRunner, Gate, Extension |

## Architecture

### Core only (Paper Trade minimum)

```
PushIngest → LiveFeatureBridge → FreshnessGate → ExposureGate(PBv2)
  → OrOverlay → ObserverPaperBook → LiveSessionWriter → Summary
```

Components: Universe, PUSH, price ring, board data, freshness, PBv2, OR, EXIT, virtual paper order (observer), summary.

### Core + Extension

```
CoreRuntime (above)
  + ExtensionBus[
      LiveOrder, Capital, Notifier, Audit JSONL,
      Counterfactual/Shadow, Runtime Trace, Report,
      Startup Cache, Config SHA, Preflight, Volume Shadow
    ]
  hooks: on_push_tick (read-only), on_post_eval, on_post_accept, on_session_end
```

## ENTRY phase call inventory

### ENTRY前 (before PBv2)

| Order | Function | File | Layer |
|-------|----------|------|-------|
| 1–3 | push → `_process_push_payload` | pilot_runner | Core |
| 4 | `begin_symbol_eval` | entry_scan_controller | Core |
| 5 | `EntryLatencyTraceSession.begin_push` | entry_latency_trace | **Extension** |
| 6 | `record_push_board_tick` | realtime_board_exit_shadow | **Extension** |
| 7–15 | feature bridge, trade build, rings, fields | various | Core |
| 16 | `observer.on_tick` (if open) | observer_position_tracker | Core |
| 17–20 | am_pm, score v2, freshness | various | Core |

### ENTRY中 (gate)

| Order | Function | File | Layer |
|-------|----------|------|-------|
| 1–6 | guard enrich → `_evaluate_gate_entry` → OR | pilot_runner, exposure_gate | Core |
| 7–8 | candidate event + audit | pilot_runner, entry_scan_controller | Core + **Extension** |
| 9–10 | volume shadow, latency trace finish | various | **Extension** |

### ENTRY後 (post-accept)

| Order | Function | File | Layer |
|-------|----------|------|-------|
| 1–6 | scan queue, execute accept, observer, writer | Core |
| 7–11 | live_order, capital, discord, shadows | **Extension** |
| 12–13 | exit dispatch, live_order exit | Core + Extension |

Full matrix: `results/reports/phase615_runtime_responsibility_matrix.csv`

## File classification

`results/reports/phase615_runtime_file_map.csv` — columns:

- File, Location, Responsibility, Core/Extension, ENTRY前影響, ENTRY後のみ

`results/reports/phase615_core_vs_extension.csv` — component-level taxonomy.

## Three-variant diff

| Module | pre625 | HEAD | pre625_mode |
|--------|--------|------|-------------|
| vol_liq_startup_cache | OFF | ON | OFF |
| live_order_* | OFF | ON | OFF |
| board_fallback | OFF | OFF (default) | OFF |
| volume_gate_shadow | OFF | ON | OFF |
| entry_scan_audit | ON | ON | ON |
| board_exit_shadow tick | ON | ON | ON |
| entry_latency_trace | OFF | opt-in | opt-in |

## Mandatory answers

1. **CoreだけでPaperTradeは成立するか** — **Yes**. Observer virtual hold + ExposureGate + LiveWriter suffice.

2. **Extension全停止でもPBv2は動くか** — **Yes**. `evaluate_entry` path unchanged (phase610/612A).

3. **ExtensionがENTRY前へ侵入している箇所** — `entry_latency_trace`, `realtime_board_exit_shadow`, `classic_momentum_forward_shadow`, `or_overlay.record_day_tick`, `stop_low_mfe.ingest_push`, `vol_liq_startup_cache` (gate build), audit side effects in scan batch.

4. **Coreへ戻すべき処理** — price ring, HBRecent, board imbalance field, entry score v2 fields (rename de-shadow).

5. **Extensionへ追い出すべき処理** — audit JSONL per-eval, latency trace, pre-tick shadows, live_order stack, volume shadow, session-end auto packs.

6. **pilot_runnerから分離できる責務** — extension inits, live_order hooks, shadow finalize, audit enrich, discord, trace wiring → `ExtensionBootstrap` / `ExtensionBus`.

7. **pre625_mode → CoreRuntimeMode** — **Yes**. Replace flag bundle with `CORE_ONLY | CORE_PLUS_AUDIT | FULL_EXTENSION` enum + extension registry.

8. **推奨アーキテクチャ** — `PaperTradeCore` pipeline + optional `ExtensionBus` with explicit lifecycle hooks; no Extension code in freshness/PBv2 hot path.

## Run

```powershell
$env:PYTHONPATH="src;.."
python scripts/run_phase615_core_extension_runtime_separation.py
```

## Outputs

- `results/reports/phase615_report.json`
- `results/reports/phase615_*.csv`
- `results/reports/phase615_runtime_*.drawio`
