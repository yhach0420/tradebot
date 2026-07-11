# ADR-687W4 — Runtime Dry-Run Wiring + Kabu Read-Only

- **Status:** Accepted (Phase687W4)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w4_runtime_readonly_latency/`

## Context

W2/W3 SafetySM was NOT_CONNECTED to Paper Runtime. Need dry-run intents from actual ENTRY/EXIT and live read-only account reconciliation without enabling submits.

## Decision

1. Wire SafetySM via `live_order_runtime_bridge` on actual accepted ENTRY and structural EXIT only.
2. Shadow/reject/capacity/notification sources never create intents.
3. `KabuBrokerAdapter` implements read-only APIs; submit/cancel/flatten remain HARD_FAIL.
4. Distinguish API failure vs zero balance vs empty positions (no silent mock fallback).
5. Measure SafetySM additive latency separately from market data freshness.
6. Weekend readonly unavailable is recorded explicitly — Mock PASS ≠ live readonly PASS.
7. Forward soak (Mon+ ≥3 sessions) is distinct from implementation READY.

## Alternatives

| Alternative | Rejected because |
|-------------|------------------|
| Reuse Phase591 only | Weaker idempotency / recon |
| Enable order_enabled for soak | Forbidden |
| Silent mock fallback on API fail | Hides capital risk |

## Consequences

- Runtime wiring: IMPLEMENTED_DRYRUN
- Kabu read: IMPLEMENTED_READONLY when Station/token available; else explicit unavailable
- Production enablement: NOT_AUTHORIZED / NOT_IMPLEMENTED

## Rollback

Set `live_order_safety_sm_enabled: false`. Do not enable live trading.

## Evidence

`results/reports/phase687w4_runtime_readonly_latency/`
