# Live Order Data Specification

**Code SoT:** dataclasses / journal writers in `live_order_safety_sm.py`

---

## 1. Persistence policy

| File | Mode | Status |
|------|------|--------|
| `order_intents.jsonl` | append-only | IMPLEMENTED_DRYRUN |
| `order_state_events.jsonl` | append-only | IMPLEMENTED_DRYRUN |
| `broker_reconciliation.jsonl` | append-only | IMPLEMENTED_DRYRUN |
| `capital_reservations.jsonl` | append-only | IMPLEMENTED_DRYRUN |
| `kill_switch_events.jsonl` | append-only | IMPLEMENTED_DRYRUN |

Common journal envelope (Phase687W4): `schema_version`, `session_id`, `sequence`, `event_time`, `monotonic_sequence`.

In-memory structures are authoritative during a process; journals are audit + restore source.

---

## 2. Identifiers (required concepts)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| session_id | str | yes | e.g. `20260711/dryrun_w2` |
| position_id | str | yes | ties ENTRY/EXIT |
| intent_id | str | logical | **code uses `order_id`** as intent id |
| idempotency_key | str | yes | sha256 hex[:32] |
| broker_order_id | str | nullable | empty until submit/ack |
| event_sequence | int | partial | **code uses `intent_seq` on engine; per-event seq NOT_IMPLEMENTED** |
| symbol | str | yes | |
| side | str | yes | BUY / SELL / EXIT keying |
| quantity | int | yes | shares |
| filled_quantity | int | yes | code field `filled_qty` |
| remaining_quantity | int | derived | `quantity - filled_qty` (not stored) |

---

## 3. Schemas

### OrderIntent (journal row)

| field | type | required | nullable | unit | source | example | sensitive | persistence |
|-------|------|----------|----------|------|--------|---------|-----------|-------------|
| timestamp | str ISO | yes | no | JST | engine | 2026-07-11T03:00:00+09:00 | no | order_intents.jsonl |
| order_id | str | yes | no | — | uuid | a1b2c3d4e5f6 | no | yes |
| idempotency_key | str | yes | no | — | make_idempotency_key | abc… | no | yes |
| symbol | str | yes | no | — | signal | 6976.T | no | yes |
| side | str | yes | no | — | BUY | BUY | no | yes |
| quantity | int | yes | no | shares | ctx/LOT | 100 | no | yes |
| price | float | yes | no | JPY | signal | 1000.0 | no | yes |
| dry_run | bool | yes | no | — | always true | true | no | yes |
| session_id | str | yes | no | — | engine | 20260711/… | no | yes |
| position_id | str | yes | no | — | signal | pA | no | yes |

### OrderStateEvent

| field | type | required | nullable | unit | source | example | sensitive | persistence |
|-------|------|----------|----------|------|--------|---------|-----------|-------------|
| timestamp | str | yes | no | JST | engine | … | no | order_state_events.jsonl |
| order_id | str | yes | no | — | order | … | no | yes |
| idempotency_key | str | no | yes | — | order | … | no | yes |
| symbol | str | no | yes | — | order | … | no | yes |
| from | str | yes* | no | state | transition | ACKNOWLEDGED | no | yes |
| to | str | yes* | no | state | transition | FILLED | no | yes |
| filled_qty | int | no | yes | shares | order | 30 | no | yes |
| quantity | int | no | yes | shares | order | 100 | no | yes |
| detail | str | no | yes | — | engine | full_fill | no | yes |
| dry_run | bool | yes | no | — | true | true | no | yes |
| event | str | no | yes | — | ILLEGAL_TRANSITION | ILLEGAL_TRANSITION | no | yes |

\* Normal transitions use `from`/`to`. Illegal rows use `event=ILLEGAL_TRANSITION`.

### CapitalReservation (in-memory `Reservation`)

| field | type | required | nullable | unit | source | example | sensitive | persistence |
|-------|------|----------|----------|------|--------|---------|-----------|-------------|
| reservation_id | str | yes | no | — | uuid | … | no | memory only |
| symbol | str | yes | no | — | ENTRY | 6976.T | no | memory |
| quantity | int | yes | no | shares | ENTRY | 100 | no | memory |
| capital_yen | float | yes | no | JPY | price*qty/leverage | 50000 | no | memory |
| slot | bool | yes | no | — | true | true | no | memory |
| released | bool | yes | no | — | lifecycle | false | no | memory |
| filled_qty | int | yes | no | shares | fills | 30 | no | memory |

JSONL for reservations: **NOT_IMPLEMENTED**.

### PositionRecord

| field | type | required | notes | persistence |
|-------|------|----------|-------|-------------|
| symbol | str | yes | key | memory `open_positions` |
| quantity | int | yes | shares | memory |

### BrokerOrderSnapshot (`BrokerOrder`)

| field | type | required | nullable | example |
|-------|------|----------|----------|---------|
| broker_order_id | str | yes | no | MOCK-00001 |
| symbol | str | yes | no | 6976.T |
| side | str | yes | no | BUY |
| quantity | int | yes | no | 100 |
| filled_qty | int | yes | no | 30 |
| status | str | yes | no | PARTIAL |
| limit_price | float | no | yes | 1000.0 |

### BrokerPositionSnapshot

`dict[str, int]` from `get_positions()`.

### ReconciliationDiff (journal / return dict)

| field | type | example |
|-------|------|---------|
| type | str | broker_only_position |
| symbol | str | 6981.T |
| local | int | 0 |
| broker | int | 100 |
| broker_order_id | str | optional |
| timestamp | str | reconcile write |
| dry_run | bool | true |

Types implemented: `quantity_mismatch`, `broker_only_position`, `local_only_position`, `broker_only_order`, `local_only_order`.

### KillSwitchEvent

| field | status |
|-------|--------|
| Dedicated JSONL | NOT_IMPLEMENTED |
| In-memory | `kill_switch`, `kill_reasons[]`, Discord kind `KILL SWITCH` |

### EntrySignal / ExitSignal (engine inputs)

Not separate dataclasses. ENTRY kwargs: `symbol`, `price`, `position_id`, `ctx`.  
EXIT kwargs: `symbol`, `quantity?`, `exit_reason`, `position_id`.

### DryRunExecution

Adapter response fields: `ok`, `status`, `broker_order_id`, `filled_qty`, `dry_run`, `would_submit` (DryRun only).

### AccountStatus

From `get_account_status()`: `online`, `token_valid`, `equity`, `buying_power` (Mock).

### SafetyOrder (runtime order object)

| field | type | notes |
|-------|------|-------|
| order_id | str | |
| idempotency_key | str | |
| side | str | |
| symbol | str | |
| quantity | int | |
| state | OrderLifecycleState | |
| session_id | str | |
| position_id | str | |
| intent_sequence | int | |
| reservation_id | str | |
| broker_order_id | str | |
| filled_qty | int | |
| exit_reason | str | |
| reject_reason | str | legacy short string |
| created_at | str | |
| illegal_transitions | list[str] | |

---

## 4. Sensitive data

Do **not** persist API tokens, passwords, or account numbers in order journals. Current writers do not include them.

---

## 5. KabuOrderRequest / fingerprint (Phase687W5)

Field names from `live_order_api_wiring.py` SoT:

| Field | ENTRY | EXIT | Notes |
|-------|-------|------|-------|
| Symbol | yes | yes | kabu code without `.T` |
| Exchange | SOR(9)/TSE+(27); TSE(1) only maintenance exception | must match open position | normal NEW Exchange=1 FORBIDDEN |
| Side | `"2"` buy | `"1"` sell | |
| Qty | ≥100, %100==0 | ≤ holding | |
| FrontOrderType | 20 limit (dry-run) | 10/20 | IOC/reverse NOT_IMPLEMENTED |
| Price | limit >0 | 0 if market | |
| ExpireDay | yes | yes | |
| FundType | omit (auto 11) or `"11"` | same | intentional omission audited |
| ClosePositions / ClosePositionOrder | forbidden | XOR one of | order=0 = date_asc_pnl_desc; production review required |

Fingerprint excludes: token, password, authorization, account number, runtime timestamps.

Stored with request: `request_fingerprint`, `canonical_payload_hash`, `schema_version`, `builder_version`.

ExecutionPolicy: `NOT_SELECTED` | `DRYRUN_*` | `PRODUCTION_FORBIDDEN`; always `production_authorized=false`.
ExchangePolicy: `NOT_SELECTED` | `SOR` | `TSE_PLUS` | `TSE_MAINTENANCE_EXCEPTION` | `REPAY_MATCH_OPEN_POSITION_EXCHANGE` | `PRODUCTION_FORBIDDEN`.

### BrokerPositionLot (Phase687W5B)

| field | artifact | runtime |
|-------|----------|---------|
| masked_hold_id | yes | yes |
| raw_hold_id | **no** | yes (local only) |
| symbol, side, leaves_quantity | yes | yes |
| exchange, margin_trade_type, account_type | yes | yes |
| entry_price, position_open_date | yes | yes |

Capability statuses: VERIFIED_FROM_LIVE_POSITION | VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE | CONFIG_ONLY | FIXTURE_ONLY | SYNTHETIC_ONLY | NOT_VERIFIED | CONFLICT | LIVE_API_NO_POSITIONS | UNKNOWN.  
Provenance: LIVE_API_ACCOUNT_RESPONSE | LIVE_API_POSITION_RESPONSE | LIVE_API_ORDER_RESPONSE | CONFIG | FIXTURE | SYNTHETIC | UNKNOWN.  
Wiring MarginTradeType=3 alone → CONFIG_ONLY (never VERIFIED).  
`fixture_live_shaped_*` → FIXTURE (never LIVE). Zero live positions → LIVE_API_NO_POSITIONS / MTT NOT_VERIFIED.

### Production enablement approval artifact (Phase687W6)

Schema-only. Required fields: approval_id, approved_by, approved_at, expires_at, git_commit, config_sha256, design_schema_version, approved_execution_policy_id, approved_exchange_policy, approved_close_policy, max_order_count, max_quantity, max_notional_yen, single_session_only, approval_status.  
Statuses: NOT_AUTHORIZED | APPROVED | EXPIRED | REVOKED | MISSING.  
W6 sample always `approval_status=NOT_AUTHORIZED`. No secrets / signing keys.  
Canary plan schema is structure-only; `canary_execution_forbidden=true`.  
Gate output fields: blocker_count, blockers, soak_status, provenance_status, capability_status, policy_status, reconciliation_status, latency_status, approval_status, production_ready, write_adapter_present, submit_hard_fail.  
`production_ready` remains false while enablement is NOT_AUTHORIZED / NOT_IMPLEMENTED.

### Operational recovery artifacts (Phase687W7)

- `session_manifest.json` — create_then_update at SafetySM start/end (no secrets)
- `session_seal.json` — relative_path, size, SHA256, row_count, schema_version, generated_at
- Journal integrity statuses: JOURNAL_OK | JOURNAL_PARTIAL_TAIL | JOURNAL_SEQUENCE_GAP | JOURNAL_DUPLICATE | JOURNAL_STATE_CONFLICT | JOURNAL_SCHEMA_MISMATCH | JOURNAL_CORRUPTED
- Recovery modes: NORMAL | ENTRY_BLOCKED | EXIT_ONLY | RECONCILIATION_REQUIRED | JOURNAL_RECOVERY_REQUIRED | KILL_SWITCH_ACTIVE | READONLY_DEGRADED | MANUAL_REVIEW_REQUIRED
- `operator_recovery_ack.json` statuses: SAMPLE_ONLY | NOT_ACKNOWLEDGED | ACKNOWLEDGED_DRYRUN | PRODUCTION_FORBIDDEN
- Audit bundle excludes: token, password, account_number, raw HoldID, authorization_header, unnecessary raw PUSH

### Stateful recovery + full session seal (Phase687W7A)

- `restore_from_journal()` restores orders, open reservations, net positions, kill-switch from JSONL; `resubmit=false` always
- Capital events: `reserve` | `apply_fill` | `release_remainder` | `release_all`
- Full seal required artifacts listed in design schema `session_seal_required_artifacts`
- Seal statuses: SEALED_VALID | INCOMPLETE | SESSION_SEAL_INVALID
- W4S extra READY: session_manifest_status=COMPLETE, session_seal_status=SEALED_VALID, journal_restore_status=JOURNAL_OK, post_seal_mutation_detected=false
- Forward: FORWARD_SESSION_SEAL_PENDING until live Paper confirms

### Recovery assertion counts (Phase687W7A1)

- `restored_order_aggregate_count`, `restored_intent_count`, `restored_entry_intent_count`, `restored_exit_intent_count`
- `restored_active_reservation_count` vs `restored_reservation_record_count`
- `restored_reserved_quantity`, `restored_reserved_notional_yen`
- `restored_position_count`, `restored_position_quantity`
- capital_reserved: intent=0, order_aggregate=1, active_reservation=1
- kill_switch_active: HOLD_UNTIL_OPERATOR (active reservation=1)
- Oracle fields: assertion_count, assertion_failure_count, unexpected_restored_object_count, test_oracle_version

### W4S seal propagation (Phase687W7A2)

- Snapshot seal fields copied from session_seal.json SoT after verify
- `session_seal_required_count`, `session_seal_verified`, `session_seal_generated_at`,
  `session_seal_schema_version`, `session_seal_manifest_sha256`, `seal_propagation_status`
- final_snapshot_sha256 on seal only; soak overlay does not invalidate seal
- W4S success requires SEAL_PROPAGATION_OK and entry_count == required_count == 14 (synthetic)
