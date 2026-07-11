# ADR-687W7: Operational Recovery and Audit Drill

- **Status:** Accepted (dry-run recovery foundation — orders remain forbidden)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w7_operational_recovery/`

## Context

W2–W6 delivered SafetySM, readonly soak, contracts, capability provenance, and a production enablement gate. Operators still need dry-run drills for restart, journal corruption, kill switch, disk/clock faults, and audit packaging — without enabling real orders.

## Decision

### PRODUCTION ORDER ENABLEMENT

**NOT AUTHORIZED / NOT IMPLEMENTED**

READY means the **dry-run operational recovery foundation** is complete — not order authorization, write adapter, or canary enablement.

### Recovery modes

Explicit modes: NORMAL, ENTRY_BLOCKED, EXIT_ONLY, RECONCILIATION_REQUIRED, JOURNAL_RECOVERY_REQUIRED, KILL_SWITCH_ACTIVE, READONLY_DEGRADED, MANUAL_REVIEW_REQUIRED.

Each mode defines ENTRY/EXIT/cancel/readonly/journal/Discord/operator action/return condition.  
**Never auto-return to NORMAL** — requires condition verification + explicit operator acknowledgment.

### Session manifest / seal

- Manifest created at SafetySM session start (`create_then_update`); finalized at session end.
- Seal is a SHA256 hash manifest of key artifacts for post-session integrity / SoT checks.
- No secrets in manifest or seal.

### Journal integrity

Statuses: JOURNAL_OK | PARTIAL_TAIL | SEQUENCE_GAP | DUPLICATE | STATE_CONFLICT | SCHEMA_MISMATCH | CORRUPTED.  
Non-OK → block new ENTRY. Partial tail: keep original; write recovery copy only.

### Kill switch / restart drills

Dry-run scenarios A–E (manual kill, pending CANCEL_REQUIRED without real cancel, journal failure, recon EXIT_ONLY, restart restore).  
Restart points verify: no resubmit, no duplicate intent, submit/cancel=0, restart_count on manifest.

### File failure / disk / clock

Persistence failure before intent → would-submit forbidden, ENTRY block, kill/recovery required; no in-memory-only continue.  
Disk: warning/critical/hard-stop thresholds; cleanup candidates only — **no auto-delete** of raw PUSH/canonical.  
Clock: diagnose only; do not change OS time sync; invalidate latency samples on anomaly.

### Operator acknowledgment / audit bundle

Schema-only `operator_recovery_ack.json` with SAMPLE_ONLY / NOT_ACKNOWLEDGED / ACKNOWLEDGED_DRYRUN / PRODUCTION_FORBIDDEN.  
No valid production ack in this phase. Audit bundles exclude tokens, passwords, account numbers, raw HoldID, auth headers, unnecessary raw PUSH.

### Separation from production authorization

W7 recovery readiness ≠ W6 production enablement. Exit 0 on recovery CLI means dry-run recovery ready only.

### CLI

`python -m small_paper.check_live_order_recovery_readiness`  
Exit: 0 dry-run ready / 2 journal·recon / 3 kill·ack / 4 disk·clock / 5 design·config.

## Consequences

- Module: `src/small_paper/operational_recovery.py`
- CLI: `src/small_paper/check_live_order_recovery_readiness.py`
- Thin pilot hooks for manifest/seal only (no strategy/order changes)
- Write adapter / canary / valid approval remain forbidden
