# ADR-687W6: Production Enablement Governance Gate

- **Status:** Accepted (governance gate only — orders remain forbidden)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w6_production_enablement_gate/`

## Context

Phases W2–W5B1 built read-only soak, request contracts, capability provenance, and policy shadow evaluation. Real order enablement still requires a machine-readable, fail-closed authorization gate. This phase implements that gate **without** implementing or enabling any write adapter.

## Decision

### PRODUCTION ORDER ENABLEMENT

**NOT AUTHORIZED / NOT IMPLEMENTED**

READY for this phase means the **governance gate is complete**, not that orders are authorized, implemented, or enabled.

### Fail-closed blocker gate

Production enablement is refused unless all required conditions PASS, including:

- W4S sessions ≥ 3 with verdict `READONLY_SOAK_READY`
- ≥1 readonly success session
- mapping loss / duplicate intent / reservation leak / submit / cancel / unexplained recon mismatch = 0
- latency p95 computed; SafetySM SLA; journal restore; kill switch drill
- live API provenance confirmed; capability not FIXTURE/SYNTHETIC; MarginTradeType live-verified
- ENTRY exchange policy, ENTRY/EXIT order style, EXIT exact HoldID close explicitly approved
- config SHA match; design consistency PASS; documentation review PASS
- explicit operator approval artifact present and not expired

Missing / unknown / unset evidence → **BLOCKED**. Boolean defaults are **False**. Never default enabling flags to True.

### Approval artifact

Schema-only sample with `approval_status=NOT_AUTHORIZED`. No valid APPROVED approval is generated in this phase. No credentials or signing keys stored.

### Canary plan

Structure only (max 1 order, max 100 shares, single ENTRY, kill switch, session expiry, EXIT path before ENTRY). **Canary execution forbidden** in this phase.

### CLI

`python -m small_paper.check_production_enablement_readiness`

| Exit | Meaning |
|------|---------|
| 0 | Technical conditions PASS but still NOT_AUTHORIZED (orders remain disabled) |
| 2 | Soak insufficient |
| 3 | Capability / policy / approval insufficiency |
| 4 | Reconciliation / safety failure |
| 5 | Design / config mismatch |

Exit 0 does **not** enable real orders.

### Write adapter

- Production write adapter: **NOT_IMPLEMENTED**
- Kabu submit/cancel/flatten: **HARD_FAIL**
- Gate/CLI must not mutate `live_trading_enabled` / `order_enabled`
- Network write call count remains 0

## Consequences

- Module: `src/small_paper/production_enablement_gate.py`
- CLI: `src/small_paper/check_production_enablement_readiness.py`
- Monday W4S Forward still required before any live provenance / MTT verification can clear soak/capability blockers
- Future APPROVED approval + write adapter remain separate, explicitly gated phases
