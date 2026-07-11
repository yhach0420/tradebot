# ADR-687W5A: Official Kabu Sendorder Contract Reconciliation

- **Status:** Accepted (schema reconciliation only — no write API)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w5a_official_contract_reconciliation/`

## Context

Phase687W5 built OrderRequestBuilder from in-repo `live_order_api_wiring.py`. Official kabusapi sendorder rules (especially Exchange) differ from the historical Exchange=1 fixtures.

## Decision

### Source of Truth priority

1. Official kabu Station API reference (`https://kabucom.github.io/kabusapi/reference/index.html`)
2. Repo vendor snapshot `docs/live_trading/vendor/kabusapi_sendorder_contract.json`
3. `live_order_api_wiring.py`
4. `OrderRequestBuilder`
5. Test fixtures

Do not invent enums. Diff official vs internal before changing wiring defaults.

### Exchange

- Official: normal-time NEW must not use Exchange=1 (TSE). Use SOR(9) or TSE+(27). TSE allowed for NEW only under maintenance exception (cash-focused note in official docs); TradeBot marks `TSE_MAINTENANCE_EXCEPTION` as explicit fixture-only.
- EXIT/repay: Exchange must match open position market (`REPAY_MATCH_OPEN_POSITION_EXCHANGE`). No silent remap to SOR/TSE+.
- Production SOR vs TSE+ selection: **NOT_SELECTED**.

### Transaction types

- TradeBot primary: `MARGIN_NEW_BUY`, `MARGIN_REPAY_SELL` (IMPLEMENTED_DRYRUN)
- `CASH_BUY` / `CASH_SELL`: **NOT_IMPLEMENTED** → builder rejects

### FundType

- Margin: omit (auto 11) or explicit `"11"`. Omission is intentional and audited.
- Cash FundType rules: not implemented (cash blocked).

### ClosePositions XOR

- Exactly one of `ClosePositions` or `ClosePositionOrder` for repay.
- `ClosePositionOrder=0` means official `date_asc_pnl_desc` — not an arbitrary TradeBot default. Production adoption requires policy review.

### MarginTradeType

- Wiring default remains 3 (一般信用デイトレ) as historical design recommendation.
- Live account verification: **NOT_VERIFIED** in this phase. Fixtures must set `margin_trade_type_source` explicitly. Do not invent system/general/daytrade from guesswork.

### Network

- No sendorder/cancel/flatten network calls.
- `request_valid_for_submit=false`; production ExecutionPolicy forbidden.

## Consequences

- Builder version `687W5A.1`
- Consistency script: `scripts/check_kabu_sendorder_contract_consistency.py`
- READY does not authorize production submit or exchange-policy production selection
