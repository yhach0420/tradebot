# ADR-687W5B: Account Capability and Execution Policy Shadow

- **Status:** Accepted (shadow collection readiness — no production policy selection)
- **Date:** 2026-07-11
- **Amended:** Phase687W5B1 (2026-07-11) — capability provenance hardening
- **Evidence:** `results/reports/phase687w5b_account_execution_policy_shadow/`
- **Provenance fix evidence:** `results/reports/phase687w5b1_capability_provenance/`

## Context

W5A reconciled official sendorder constraints. Production still must not choose SOR vs TSE+, MARKET vs LIMIT, or ClosePositionOrder=0. This phase adds **read-only account capability** and **shadow evaluation** that can run alongside W4S soak.

## Decision

### Provenance (W5B1 — mandatory)

Explicit classes only:

- `LIVE_API_ACCOUNT_RESPONSE`
- `LIVE_API_POSITION_RESPONSE`
- `LIVE_API_ORDER_RESPONSE`
- `CONFIG`
- `FIXTURE`
- `SYNTHETIC`
- `UNKNOWN`

Strings containing `live_shaped` / `fixture` normalize to **FIXTURE**, never LIVE.

### MarginTradeType verification hierarchy

1. `VERIFIED_FROM_LIVE_POSITION` — **only** when:
   - provenance = `LIVE_API_POSITION_RESPONSE`
   - token acquired
   - positions endpoint ok
   - response timestamp present and not stale
   - fixture/synthetic flags false
   - schema validation PASS
   - each lot has MarginTradeType, Exchange, AccountType
2. `VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE` — account readable; MTT still NOT_VERIFIED from positions
3. `LIVE_API_NO_POSITIONS` — endpoint success, 0 lots → MTT **NOT_VERIFIED**
4. `CONFIG_ONLY` / wiring default (including MTT=3) — **never VERIFIED**
5. `FIXTURE_ONLY` / `SYNTHETIC_ONLY` — never policy evidence
6. Mixed fixture+live lots → `CONFLICT`
7. EXIT repay MTT **must** come from broker position with live provenance — never YAML/wiring override

### Fixture vs live boundary

- W5B `fixture_live_shaped_positions` is **FIXTURE_ONLY** / MTT **NOT_VERIFIED** (corrected in W5B1; W5B artifacts not overwritten).
- Fixture results must not drive production Execution Policy selection.
- Provenance unknown → production forbidden.

### Account capability profile

Built from read-only wallet + position lots. No account numbers, tokens, or passwords in artifacts.

### Broker HoldID mapping

- kabusapi positions lot id: `ExecutionID` (used as ClosePositions HoldID)
- Artifacts: `masked_hold_id` only; `hold_id_live_verified` only when provenance is LIVE_API_POSITION_RESPONSE
- Fixture HoldID: maskable, never live-verified
- Runtime/local may retain raw HoldID for exact close structure; never log/Discord/research raw

### Exact close policy

Priority: `CLOSE_EXACT_HOLD_ID` → `CLOSE_EXACT_MULTI_HOLD` → else `RECOVERY_REQUIRED`.  
**Forbidden:** silent fallback to `ClosePositionOrder=0` when HoldID unknown.  
`CLOSE_POSITION_ORDER_0` remains test-candidate only; production_authorized=false.

### SOR / TSE+ shadow

For each actual accepted ENTRY, generate both request candidates. No production fallback between them.

### MARKET / LIMIT shadow

ENTRY styles: MARKET, LIMIT@ask, LIMIT@last, LIMIT+tick offset.  
EXIT styles by reason (stop / no_progress / trailing / session_close).  
Future path prices are **evaluation-only** — never policy inputs.

### Fill simulation limitation

Uses compact price path / board snapshot — not raw PUSH. Incomplete data → UNKNOWN (never invent fill).

### Production policy selection

**NOT_IMPLEMENTED.** Requires ≥3 successful W4S sessions before any selection discussion.  
READY means shadow **collection** readiness only. Monday W4S Forward must record live API provenance fields.

### W4S relationship

`write_soak_session_snapshot` records W5B counters plus W5B1 provenance fields (`capability_provenance`, `fixture_used`, `margin_trade_type_live_verified`, …).

## Consequences

- Modules: `kabu_account_capability`, `kabu_position_identity`, `kabu_close_policy`, `kabu_execution_policy_shadow`
- KabuBrokerAdapter exposes `get_position_lots_raw` (read-only)
- Network submit remains HARD_FAIL / count=0
