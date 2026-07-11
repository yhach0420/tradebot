# ADR-687W2 — Order Safety State Machine

- **Status:** Accepted (Phase687W2)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w2_live_order_safety/`, `src/small_paper/live_order_safety_sm.py`

## Context

Paper Runtime already has Phase591–594 dry-run adapters, but they lack an explicit, audited order lifecycle with illegal-transition rejection, capital reservation, and timeout-safe recovery. Real broker submit remains forbidden (`live_trading_enabled=false`, `order_enabled=false`).

## Decision

1. Introduce `LiveOrderSafetyEngine` with an explicit `OrderLifecycleState` enum and one-way-biased transition matrix (`ENTRY_ALLOWED`).
2. Persist intents/state/reconcile events as **append-only JSONL**.
3. On timeout-after-submit, move to `UNKNOWN` and **reconcile only** — never blind-resubmit.
4. Ship Mock + DryRun adapters first; `KabuBrokerAdapter` hard-fails mutations.

## Alternatives

| Alternative | Why rejected |
|-------------|--------------|
| Extend Phase591 adapter only | Weaker illegal-transition audit; mixed concerns |
| Auto-resubmit on timeout | Duplicate order risk |
| Mutable state DB overwrite | Harder audit / forensics |
| Connect Kabu submit early | Violates safety gates |

## Consequences

- Clear dry-run verification path (24 fault injections, Scenarios A–E).
- Dual-stack with Phase591 adapter until Runtime wiring (`NOT_CONNECTED`).
- Operators must use reconcile/restore, not resubmit, after UNKNOWN.

## Rollback

Remove/ignore `live_order_safety_sm.py` usage; Paper Runtime unchanged. Journals remain for audit. Do not enable live flags as rollback.

## Evidence

- Verdict `LIVE_ORDER_SAFETY_DRYRUN_READY`
- Fault injection 24/24
- `actual_broker_submit_count = 0`
