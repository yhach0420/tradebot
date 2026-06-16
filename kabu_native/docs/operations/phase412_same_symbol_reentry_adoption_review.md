# Phase412 — Same-Symbol Reentry Reject Runtime Adoption Review

## Conclusion

- **Runtime反映してよいか**: NO (do not adopt)
- **理由**: verdict=`reject_runtime_adoption` (baseline vs shadow backfill gate)
- **悪化リスク**: 同一銘柄の重複ENTRYを抑止するため、短期の再ENTRYで取りに行く局面があれば機会損失になり得る。
- **rollback方法**: `same_symbol_open_reentry_policy: replace`（既存互換）

## Backfill summary (20260529–20260616)

- baseline_trade_count: 1529
- shadow_trade_count: 1065
- trade_reduction_count: 464
- baseline_total_pnl_yen_100: 130767.6
- shadow_total_pnl_yen_100: 109638.31
- delta_pnl_yen_100: -21129.29
- baseline_pf: 1.101
- shadow_pf: 1.0988
- baseline_maxdd: 105301.93
- shadow_maxdd: 89102.37
- baseline_overlap_replaced_review_count: 999
- shadow_overlap_replaced_review_count: 600
- overlap_replaced_review_reduction_count: 399

## Mandatory check — 20260616 matches Phase411

- 20260616 baseline trades: None
- 20260616 shadow trades: 394
- 20260616 shadow PnL: 23200.0
- 20260616 shadow PF: 1.169
- 20260616 shadow maxDD: 32300.0
