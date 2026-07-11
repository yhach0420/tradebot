# ADR-687W7A: Stateful Journal Recovery and Runtime Session Seal

- **Status:** Accepted (stateful dry-run proof + runtime hooks — orders remain forbidden)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w7a_stateful_recovery/`

## Context

Phase687W7 delivered recovery modes, journal integrity checks, and drill scaffolding, but restart drills did not write production-shaped journals (`restored_orders=0`). Runtime session manifest/seal hooks were incomplete (UNSET/demo values). This phase proves **stateful restore** from real append-only files and connects full session seal to Paper Runtime.

## Decision

### PRODUCTION ORDER ENABLEMENT

**NOT AUTHORIZED / NOT IMPLEMENTED**

READY means stateful restore proof + runtime integration readiness — not order authorization.

### Stateful replay

`restore_from_journal()` rebuilds from:

- `order_intents.jsonl`
- `order_state_events.jsonl`
- `capital_reservations.jsonl` (reserve / apply_fill / release_*)
- `kill_switch_events.jsonl`

Guarantees: no broker write, no automatic resubmit, final state by event order, kill switch restored, positions netted from BUY/SELL fills, open reservations only.

### Stop-point expectations

Documented A–L matrix (session_startup → kill_switch_active). Pass requires comparing restored objects (orders, reservations, positions, fills), not hardcoded `pass=true`.

### Full session seal

Required artifacts include canonical summary/events/positions/rejects, SafetySM journals, soak snapshot, NP logger files, session_manifest. Missing required → `INCOMPLETE` (not counted as W4S success). Post-seal mutation → `SESSION_SEAL_INVALID` / `MANUAL_REVIEW_REQUIRED`.

### Runtime hooks

- Session start: real `git_commit`, `config_sha256`, token/readonly status, journal sequence start; UNSET/demo → completeness INCOMPLETE
- Session end: finalize counters + full seal; duplicate finalize safe
- Bridge startup: `restore_from_journal()` before reconciliation

### W4S

Soak snapshot adds W7A fields. Extra READY conditions: manifest COMPLETE, seal SEALED_VALID, journal JOURNAL_OK, no post-seal mutation.

### Synthetic vs forward

- `SYNTHETIC_RECOVERY_PROOF_PASS` / `RUNTIME_INTEGRATION_READY` (this phase)
- `FORWARD_SESSION_SEAL_PENDING` until Monday+ live Paper confirms real seal

### Disk warning

Record start/end usage. ≥90% ENTRY safety block + not W4S success; ≥95% hard-stop. No auto-delete of canonical/raw PUSH.

## Consequences

- Module: `src/small_paper/stateful_journal_recovery.py`
- Enhanced: `LiveOrderSafetyEngine.restore_from_journal`, capital journal on fill/release
- Hooks: `pilot_runner`, `live_order_runtime_bridge`
- Write adapter / canary / production approval remain forbidden
