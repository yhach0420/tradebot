# ADR-687W7A1: Recovery Assertion Integrity

- **Status:** Accepted (oracle/count semantics fix — restore engine unchanged in principle)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w7a1_recovery_assertion_integrity/`

## Context

Phase687W7A could report `pass=true` while expected/actual counts disagreed (loose checks, ambiguous `restored_order_count`, `expected_position_count=0` on EXIT cases).

## Decision

### Count semantics

Separated fields:

- `restored_order_aggregate_count` / `restored_intent_count` / entry/exit intent counts
- `restored_active_reservation_count` vs `restored_reservation_record_count`
- `restored_reserved_quantity` / `restored_reserved_notional_yen`
- `restored_position_count` / `restored_position_quantity`

### capital_reserved

Pre-Intent:

- `expected_intent_count = 0`
- `expected_order_aggregate_count = 1` (CAPITAL_RESERVED state object)
- `expected_active_reservation_count = 1`

### Kill switch reservation — Policy A

`kill_switch_active` **holds** pending reservation (`expected_active_reservation_count=1`) until operator/recon.  
Aligns with W7 drill E (`reservation_state=unchanged`).  
Policy B release is a **separate** scenario: `kill_switch_pending_release` (not this case).

### Pass oracle

`pass` is solely `assertion_failure_count == 0` from expected/actual AND checks.  
Negative oracle must force FAIL for tampered expected/actual.  
`test_oracle_version = 687W7A1.1`.

### W4S

Added: `recovery_assertion_version`, `recovery_assertion_failure_count`, `recovery_unexpected_object_count`, `recovery_expected_actual_match`.  
READY extras require failure_count=0, unexpected=0, match=true.

## Consequences

- Module: `src/small_paper/recovery_assertion_oracle.py`
- Stateful matrix uses strict oracle
- PRODUCTION ORDER ENABLEMENT remains NOT AUTHORIZED / NOT IMPLEMENTED
