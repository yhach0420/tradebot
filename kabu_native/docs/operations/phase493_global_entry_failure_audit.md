# Phase493 — Global Entry Failure Audit

**Verdict:** `global_guard_candidate`
**Period:** 20260529 — 20260622

## Part A — Global Summary

[
  {
    "bucket": "ALL",
    "trade_count": 286,
    "win_count": 164,
    "loss_count": 107,
    "flat_count": 15,
    "total_pnl_yen_100": 244962.83,
    "profit_factor": 1.6171,
    "max_drawdown_yen_100": 53899.13,
    "stop_hit_count": 55,
    "no_progress_count": 0,
    "trailing_mfe_count": 0,
    "session_close_count": 9
  },
  {
    "bucket": "AM",
    "trade_count": 181,
    "win_count": 102,
    "loss_count": 69,
    "flat_count": 10,
    "total_pnl_yen_100": 124348.58,
    "profit_factor": 1.3886,
    "max_drawdown_yen_100": 95400.07,
    "stop_hit_count": 45,
    "no_progress_count": 0,
    "trailing_mfe_count": 0,
    "session_close_count": 3
  },
  {
    "bucket": "PM",
    "trade_count": 105,
    "win_count": 62,
    "loss_count": 38,
    "flat_count": 5,
    "total_pnl_yen_100": 120614.25,
    "profit_factor": 2.5667,
    "max_drawdown_yen_100": 21697.98,
    "stop_hit_count": 10,
    "no_progress_count": 0,
    "trailing_mfe_count": 0,
    "session_close_count": 6
  }
]

## 必須回答

1. falling_knife
2. global_problem
3. consistent_with_global_trend
4. 0.1818
5. 0.2545
6. 0.2
7. 6838
8. symbol_specific_falling_knife — guard E or re-entry cooldown shadow
9. high_price_extension / trap mix — monitor; do not exclude
10. F_high_price_extension
11. 51201.03
12. 32
13. 23
14. True
15. True
16. moderate
17. False
18. True
19. ['Verdict: global_guard_candidate', 'Forward-shadow best guard: F_high_price_extension', 'Replay Phase487-style LOO on full pool before any gate enable', '6522: same-symbol re-entry cooldown shadow (not symbol exclude)']

**Verdict:** `global_guard_candidate`
