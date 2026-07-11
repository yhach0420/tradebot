# ADR-687W5: Kabu Order Request Contract Builder

- **Status:** Accepted (Dry-run / Mock only)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w5_kabu_order_contract/`

## Context

LiveOrderSafetySM produces validated OrderIntents. Before any broker network submit is allowed, the system needs a safe conversion layer to kabusapi `sendorder` payloads with validation, fingerprinting, and mock response parsing.

## Decision

1. **Intent / Policy / Request separation**
   - Intent: what to trade (symbol, qty, side, position_id, idempotency)
   - ExecutionPolicy: how to send (schema only; production style undecided)
   - KabuOrderRequest: concrete kabusapi fields

2. **Source of Truth for fields:** `src/small_paper/live_order_api_wiring.py` (not guessed enums). External kabusapi docs are referenced; no vendored sendorder OpenAPI in-repo.

3. **Network isolation:** `OrderRequestBuilder` and `OrderResponseParser` hold no HTTP client. Submit/cancel/flatten remain HARD_FAIL on `KabuBrokerAdapter`.

4. **Fingerprint:** Canonical JSON over intent_id, idempotency_key, symbol, side, qty, policy, account classification, price/expiry fields, position_id. Excludes tokens/passwords/account numbers/runtime timestamps.

5. **Request mutation:** Same idempotency_key with changed payload → `REQUEST_MUTATION_DETECTED` → `RECOVERY_REQUIRED`. Must not be treated as submit-eligible.

6. **Timeout:** Parsed as `UNKNOWN` + reconciliation required. **No automatic resubmit.**

7. **Execution policy:** Default `NOT_SELECTED` → `request_valid_for_submit=false`. All policies `production_authorized=false`. Production market vs limit is **not** decided.

8. **Station readiness:** Process-name detection is advisory (`station_process_detected`). Availability uses `operational_api_available` (token + read-only). Process false alone does not force `KABU_STATION_NOT_RUNNING` when API works.

## Consequences

- Request builder: `IMPLEMENTED_DRYRUN`
- Response parser: `IMPLEMENTED_MOCK`
- Execution policy selection: `NOT_IMPLEMENTED`
- Network submit: `PRODUCTION_FORBIDDEN`
- Real broker ACK latency: `UNMEASURED`

## Production blockers

- No production ExecutionPolicy authorization
- No network submit adapter wiring
- live_trading_enabled / order_enabled remain false
- Real account order numbers must not be persisted as durable SoT from live probes
