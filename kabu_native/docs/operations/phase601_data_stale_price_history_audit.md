# Phase601 data_stale_price Introduction History Audit

**Verdict:** `phase601_data_stale_price_history_audit_done`

## Mandatory answers

1. 2026-06-13
2. NP-entry-scan (kabutrade0612); observability Phase490
3. CurrentPriceTime missing OR age>entry_max_price_age_sec(3.0)
4. 3.0
5. False
6. True
7. False
8. CurrentPriceTime frozen ~10:11 while board Bid/Ask updates at eval time (price_age~9900s)
9. payload.CurrentPriceTime from kabu PUSH
10. low_for_guard_logic; feed_or_timestamp_staleness_for_some_symbols
11. False
12. phase602_push_replay_clock_parity_and_price_ts_fallback_study

## Outputs

- `phase601_data_stale_price_history.csv`
- `phase601_data_stale_price_threshold_history.csv`
- `phase601_data_stale_price_daily_rate.csv`
- `phase601_data_stale_price_symbol_breakdown_20260629.csv`
- `phase601_price_timestamp_source_audit.csv`
- `phase601_report.json`