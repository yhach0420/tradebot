# ADR-687W3 — E2E Read-only Reconciliation + Design Spec Alignment

- **Status:** Accepted (Phase687W3 design + consistency gates)
- **Date:** 2026-07-11
- **Evidence:** `docs/live_trading/`, `results/reports/phase687w3_e2e_readonly_reconciliation/`

## Context

Phase687W2 delivered a dry-run state machine without a formal design SoT. Phase687W3 must not claim READY from code alone: design docs must match code, and Kabu must start as **read-only / not connected**, never as submit-capable.

## Decision

1. Establish `docs/live_trading/*` as Source of Truth with explicit status tags (`IMPLEMENTED_*` / `NOT_*` / `PRODUCTION_FORBIDDEN`).
2. Add machine-readable `schema/live_order_design_schema.json` and `scripts/check_live_order_design_consistency.py`; mismatch → test failure and READY forbidden.
3. Keep Kabu adapter mutations hard-fail; treat live account read as `NOT_CONNECTED` until a future readonly soak.
4. Distinguish capital **API failure** vs **zero balance** in error taxonomy.
5. On reconciliation mismatch → exit-only / `RECOVERY_REQUIRED`, never invent ENTRY to sync.
6. Wire design-required aliases (`receive_*`, `restore_from_journal`, `get_recent_executions`) only where they match real behavior.

## Alternatives

| Alternative | Why rejected |
|-------------|--------------|
| Docs-only without consistency checker | Drift → false READY |
| Connect Kabu submit behind flag | Premature; hard-fail preferred |
| Treat 0 BP same as API down | Wrong recovery action |
| Auto-flatten on mismatch | Too dangerous; Mock-only flatten |

## Consequences

- READY requires design consistency + documentation review PASS.
- New verdicts: `DESIGN_SPEC_INCOMPLETE`, `DESIGN_CODE_MISMATCH`.
- Production order enablement remains `NOT AUTHORIZED / NOT IMPLEMENTED`.

## Rollback

Keep W2 engine; mark W3 docs/checker inactive. Do not enable live trading as rollback.

## Evidence

- Five design docs + two ADRs
- `phase687w3_design_consistency.json`
- `phase687w3_documentation_review.json`
- `phase687w3_requirement_traceability.csv`
- W2 fault injection still PASS; submit count 0
