# Live Order Interface Specification

**Code SoT:** `src/small_paper/live_order_safety_sm.py`  
**Error codes:** `src/small_paper/live_order_error_codes.py`

Status tags apply per method × mode.

---

## 1. BrokerAdapter

Base class methods. Implementations: Mock (`IMPLEMENTED_MOCK`), DryRun (`IMPLEMENTED_DRYRUN`), Kabu (`PRODUCTION_FORBIDDEN` for mutations; reads `NOT_CONNECTED` skeleton).

| Method | Args | Returns | Nullable | Timeout | Retry | Idempotency | Errors | Dry-run | Read-only | Production |
|--------|------|---------|----------|---------|-------|-------------|--------|---------|-----------|------------|
| `get_account_status()` | — | `dict` (`online`, `token_valid`, …) | values may be absent | N/A (sync) | no | n/a | RuntimeError if unimplemented | Mock account | Kabu returns offline skeleton | NOT_CONNECTED |
| `get_buying_power()` | — | `float` | no | N/A | no | n/a | RuntimeError / offline | Mock equity BP | Kabu raises unavailable | NOT_CONNECTED |
| `get_positions()` | — | `dict[str,int]` | empty ok | N/A | no | n/a | — | Mock positions | Kabu `{}` | NOT_CONNECTED |
| `get_open_orders()` | — | `dict[str,BrokerOrder]` | empty ok | N/A | no | n/a | — | Mock open orders | Kabu `{}` | NOT_CONNECTED |
| `get_recent_executions()` | — | `list[dict]` | empty ok | N/A | no | n/a | — | Mock `recent_executions` | Kabu `[]` | NOT_CONNECTED |
| `get_order_status(broker_order_id: str)` | id required | `dict` status | — | N/A | no | n/a | NOT_FOUND | Mock lookup | **IMPLEMENTED_READONLY** | PRODUCTION_FORBIDDEN for mutations only |
| `reconcile_order(broker_order_id: str)` | id | `dict` (default→get_order_status) | — | N/A | no | n/a | NOT_FOUND | Mock | Read-only | PRODUCTION_FORBIDDEN for mutations |

### Phase687W4T readiness CLI

`python -m small_paper.check_kabu_readonly_readiness`

Exit codes: 0 READY / 2 station-or-token / 3 auth-config / 4 response-invalid / 5 safety-invariant.

Token retry: max 3; AUTH_FAILED no retry. Credentials never printed.

| `submit_entry_order(intent: Mapping)` | intent | `dict` (`ok`, `status`, …) | — | simulated via TimeoutError | **no auto-retry** | caller key in intent | reject/timeout | would-submit | N/A | **HARD_FAIL** on Kabu |
| `submit_exit_order(intent: Mapping)` | intent | `dict` | — | N/A | no | caller key | exit_qty_exceeds | would-submit | N/A | **HARD_FAIL** on Kabu |
| `cancel_order(broker_order_id: str)` | id | `dict` | — | N/A | no | n/a | NOT_FOUND | Mock cancel | N/A | **HARD_FAIL** on Kabu |
| `emergency_flatten()` | — | `dict` | — | N/A | no | n/a | — | Mock clears positions | N/A | **HARD_FAIL** on Kabu |

### Intent mapping (submit_*)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| symbol | str | yes | |
| quantity | int | yes | 100-lot |
| limit_price | float | ENTRY | |
| idempotency_key | str | yes (ENTRY) | |
| dry_run | bool | yes | must be true in this phase |
| exit_reason | str | EXIT | |

---

## 2. LiveOrderSafetyEngine

| Method | Status | Notes |
|--------|--------|-------|
| `handle_entry_signal(*, symbol, price, position_id, ctx=None)` | IMPLEMENTED_DRYRUN | Primary ENTRY API |
| `receive_entry_signal(**kwargs)` | IMPLEMENTED_DRYRUN | Alias → `handle_entry_signal` |
| `handle_exit_signal(*, symbol, quantity=None, exit_reason=..., position_id="")` | IMPLEMENTED_DRYRUN | Primary EXIT API |
| `receive_exit_signal(**kwargs)` | IMPLEMENTED_DRYRUN | Alias → `handle_exit_signal` |
| `precheck(*, symbol, price, ctx)` | IMPLEMENTED_DRYRUN | Returns `(ok: bool, reason: str)` |
| `reserve_capital(*, symbol, quantity, capital_yen)` | IMPLEMENTED_DRYRUN | Wraps `CapitalLedger.reserve` |
| `transition(order, new_state, *, detail="")` | IMPLEMENTED_DRYRUN | Rejects illegal; audits |
| `reconcile_unknown(order_id)` | IMPLEMENTED_DRYRUN | UNKNOWN only; no resubmit |
| `reconcile(order_id)` | IMPLEMENTED_DRYRUN | Alias → `reconcile_unknown` |
| `startup_reconciliation(*, local_positions, local_pending)` | IMPLEMENTED_DRYRUN | Diff classify |
| `restore_from_journal()` | IMPLEMENTED_DRYRUN | Rebuild memory; **never resubmits** |
| `activate_kill_switch(reason)` | IMPLEMENTED_DRYRUN | Blocks ENTRY |
| `release_reservation(reservation_id)` | IMPLEMENTED_DRYRUN | `ledger.release_all` |
| `cancel(order_id)` | IMPLEMENTED_DRYRUN | |
| `additional_fill(order_id, fill_qty)` | IMPLEMENTED_DRYRUN | Partial→full test helper |
| `actual_broker_submit_count()` | IMPLEMENTED_DRYRUN | Always 0 for mock/dryrun |

### Design names vs code

| Design name | Code | Status |
|-------------|------|--------|
| `receive_entry_signal` | alias | IMPLEMENTED_DRYRUN |
| CapitalReservationManager | `CapitalLedger` | IMPLEMENTED_DRYRUN |
| PositionLedger | `CapitalLedger.open_positions` | IMPLEMENTED_DRYRUN |
| OrderIntentStore | `AppendOnlyStore.write_intent` | IMPLEMENTED_DRYRUN |
| OrderStateJournal | `AppendOnlyStore.write_state_event` | IMPLEMENTED_DRYRUN |
| ReconciliationService | methods on engine | IMPLEMENTED_DRYRUN |
| DiscordNotifier | `_notify` | IMPLEMENTED_MOCK |
| OrderRequestBuilder | `kabu_order_request_builder.OrderRequestBuilder` | IMPLEMENTED_DRYRUN |
| OrderResponseParser | `kabu_order_response_parser.OrderResponseParser` | IMPLEMENTED_MOCK |
| ExecutionPolicy | `kabu_order_execution_policy.ExecutionPolicy` | schema only; selection NOT_IMPLEMENTED |

---

## 3. State transition table (ENTRY-focused)

| Current | Event | Next | Action | Reservation | Broker query | Retry | Invalid behavior |
|---------|-------|------|--------|-------------|--------------|-------|------------------|
| SIGNAL_RECEIVED | begin precheck | PRECHECK_PENDING | start | none | no | no | — |
| PRECHECK_PENDING | fail | PRECHECK_REJECTED | store reason | none | no | no | — |
| PRECHECK_PENDING | pass ENTRY | CAPITAL_RESERVED | reserve | create | BP read | no | — |
| PRECHECK_PENDING | EXIT path | ORDER_INTENT_CREATED | skip capital | none | no | no | — |
| CAPITAL_RESERVED | intent | ORDER_INTENT_CREATED | persist intent | held | no | no | — |
| ORDER_INTENT_CREATED | submit | SUBMIT_PENDING | call adapter | held | yes | no | — |
| ORDER_INTENT_CREATED | journal fail | CANCELED | release | release | no | no | — |
| SUBMIT_PENDING | timeout before | BROKER_REJECTED | release | release | no | no | — |
| SUBMIT_PENDING | timeout after | UNKNOWN | retain | retain | later | **no submit** | — |
| SUBMIT_PENDING | reject | BROKER_REJECTED | release | release | no | no | — |
| SUBMIT_PENDING | ok | SUBMITTED | set broker id | held | no | no | — |
| SUBMITTED | ack | ACKNOWLEDGED | notify | held | no | no | — |
| ACKNOWLEDGED | partial | PARTIALLY_FILLED | apply_fill | partial | no | no | — |
| ACKNOWLEDGED | full | FILLED | apply+release | release | no | no | — |
| PARTIALLY_FILLED | more fill | PARTIAL/FILLED | apply_fill | update | no | no | — |
| * | cancel req | CANCEL_PENDING | cancel API | — | yes | no | — |
| CANCEL_PENDING | canceled | CANCELED | release remainder | release | no | no | — |
| CANCEL_PENDING | fill race | FILLED | apply fill | release | yes | no | — |
| UNKNOWN | reconcile ACK | ACKNOWLEDGED | sync | held | yes | no | — |
| UNKNOWN | NOT_FOUND | CANCELED | release | release | yes | no | — |
| FILLED | submit_success | — | **reject** | unchanged | no | no | audit ILLEGAL_TRANSITION |
| CANCELED | broker_fill_found | RECOVERY_REQUIRED | human path | recalc | yes | no | alert (via recovery) |
| UNKNOWN | timeout again | UNKNOWN | reconcile only | retained | yes | **no submit** | recovery if stuck |

Allowed matrix source: `ENTRY_ALLOWED` in code.

---

## 4. Error code taxonomy

Formal codes in `live_order_error_codes.py`. Engine `reject_reason` still uses legacy short strings; map via `to_error_code()`.

| Code | Severity | Retryable | ENTRY | EXIT | Kill | Recovery | Discord |
|------|----------|-----------|-------|------|------|----------|---------|
| CONFIG_LIVE_TRADING_ENABLED | critical | no | no | yes* | no | disable flag | PRECHECK BLOCK |
| CONFIG_ORDER_ENABLED | critical | no | no | yes* | no | disable flag | PRECHECK BLOCK |
| CONFIG_DRY_RUN_REQUIRED | high | no | no | yes* | no | set dry_run | PRECHECK BLOCK |
| PRECHECK_KILL_SWITCH_OR_RECOVERY | high | no | no | yes | already | human | KILL/RECOVERY |
| CAPITAL_ZERO_BALANCE | high | no | no | yes | no | wait funds | PRECHECK BLOCK |
| CAPITAL_UNAVAILABLE | high | yes later | no | yes | optional | restore API | PRECHECK BLOCK |
| CAPITAL_INSUFFICIENT_MARGIN | medium | no | no | yes | no | reduce size | BROKER REJECT |
| DATA_STALE_PRICE | medium | yes later | no | yes | no | wait fresh | PRECHECK BLOCK |
| DATA_STALE_BOARD | medium | yes later | no | yes | no | wait fresh | PRECHECK BLOCK |
| BROKER_OFFLINE | high | yes later | no | limited | yes | reconnect | PRECHECK BLOCK |
| BROKER_TIMEOUT_AFTER_SUBMIT | high | **reconcile only** | no | — | no | reconcile | SUBMITTED DRYRUN UNKNOWN |
| BROKER_SUBMIT_HARD_FAIL | critical | no | no | no | no | never call Kabu submit | — |
| RECONCILIATION_MISMATCH | critical | no | no | yes | yes | exit-only | RECONCILIATION ERROR |
| JOURNAL_WRITE_FAILURE | high | no | no | — | no | fix disk | CANCEL path |
| STATE_TRANSITION_ILLEGAL | high | no | — | — | no | audit | state event |
| KILL_SWITCH_DAILY_LOSS | critical | no | no | yes | yes | human | KILL SWITCH |
| REQUEST_MUTATION_DETECTED | critical | no | no | no | yes | RECOVERY_REQUIRED | REQUEST MUTATION |
| REQUEST_POLICY_NOT_SELECTED | medium | no | no | no | no | select dry-run test policy only | — |
| REQUEST_INVALID | medium | no | no | no | no | fix payload | — |

\* EXIT still allowed by engine even when ENTRY precheck would fail for kill/recovery; config live/order flags are ENTRY precheck gates (EXIT path currently skips full ENTRY precheck).

**API failure vs zero balance are distinct:** `CAPITAL_UNAVAILABLE` / `CAPITAL_API_OFFLINE` vs `CAPITAL_ZERO_BALANCE`.

---

## 5. OrderRequestBuilder / ResponseParser (Phase687W5)

| Component | Status | Network |
|-----------|--------|---------|
| `OrderRequestBuilder.build(intent, policy)` | IMPLEMENTED_DRYRUN | none |
| `OrderResponseParser.parse(fixture)` | IMPLEMENTED_MOCK | none |
| ExecutionPolicy selection | NOT_IMPLEMENTED | n/a |
| `request_valid_for_submit` | always false this phase | — |

Fingerprint fields: `request_fingerprint`, `canonical_payload_hash`, `schema_version`, `builder_version`.  
Mutation: same idempotency_key + changed payload → `REQUEST_MUTATION_DETECTED` → `RECOVERY_REQUIRED`.  
Timeout parse: `UNKNOWN`, `auto_resubmit=false`.

### ExchangePolicy / TransactionType (Phase687W5A)

| ExchangePolicy | ENTRY | EXIT |
|----------------|-------|------|
| NOT_SELECTED | request_valid_for_submit=false | recovery |
| SOR | Exchange=9 | forbidden (use repay-match) |
| TSE_PLUS | Exchange=27 | forbidden (use repay-match) |
| TSE_MAINTENANCE_EXCEPTION | Exchange=1 explicit only | forbidden for silent use |
| REPAY_MATCH_OPEN_POSITION_EXCHANGE | invalid on ENTRY | match open position exchange |
| PRODUCTION_FORBIDDEN | reject | reject |

Cash transaction types: NOT_IMPLEMENTED. Official snapshot: `docs/live_trading/vendor/kabusapi_sendorder_contract.json`.

### Account capability / close / shadow (Phase687W5B)

| Component | Status |
|-----------|--------|
| `AccountCapabilityProfile` | IMPLEMENTED_DRYRUN |
| `BrokerPositionLot` + HoldID mask | IMPLEMENTED_DRYRUN |
| `decide_close_policy` | IMPLEMENTED_DRYRUN |
| Exchange / order-style shadow | IMPLEMENTED_DRYRUN |
| Fill simulator (compact) | IMPLEMENTED_MOCK |
| Production policy selection | NOT_IMPLEMENTED |

EXIT close priority: CLOSE_EXACT_HOLD_ID → CLOSE_EXACT_MULTI_HOLD → RECOVERY_REQUIRED (no silent Order=0).
