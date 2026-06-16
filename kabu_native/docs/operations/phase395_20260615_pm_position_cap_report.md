# Phase395 — 2026-06-15 PM Position-CAP Comparison

Generated: 2026-06-15T21:47:45+09:00

Session: `20260615/live_session_122531`

## Method

- **A. Virtual-hold CAP** — replay gate stream (`accepted` + `rejected[max_concurrent]`, n=1793) with 300s slot release (current Runtime).
- **B. Position-CAP until EXIT** — replay `structural_trades` entry timeline (n=90) with CAP=3 until structural exit; session `force_close` at 15:23.
- **C. Capital sim reference** — 1.5M / lev2 / 100 shares / CAP3 / fixed_stop_1p2 on session `structural_trades`.

Note: cap-blocked gate candidates never reached observer, so position-CAP uses the observer/canonical trade timeline (same input family as Phase267–274 capital sim).

---

## Summary

| model | accepted_count | rejected_by_cap | max_active_positions | final_pnl_yen_100 | session_close_remaining | discord_exit_1523 |
| --- | --- | --- | --- | --- | --- | --- |
| virtual_hold | 90 | 1703 | 3 | 46803.77 | 0 | 12 |
| position_cap_until_exit | 22 | 58 | 3 | 18700.0 | 0 | 12 |
| capital_sim_1500k_lev2_cap3 | 22 | 58 | 3 | 18700.0 | None | 12 |

---

## Deltas (Position-CAP minus Virtual-hold)

| Metric | Delta |
|--------|-------|
| Accepted | -68 |
| Rejected by CAP | -1645 |
| Max active positions | 0 |
| PnL (100 shares) | ¥-28103.77 |

---

## Discord / Observer Context

| Metric | Value |
|--------|-------|
| Gate `peak_open_slots` | 3 |
| Observer max open positions (structural replay) | 16 |
| Discord `observer_exit` at 15:23 | 12 |
| Position-CAP session-close remaining at 15:23 | 0 |

At 15:23, gate virtual-hold slots were **0** while observer issued **12** EXIT notifications
(session-close burst from `close_all()`). Under position-CAP, **0** positions
required end-of-session force-close in this replay (observer had up to **16** concurrent opens, uncapped).

---

## Equity Curve Impact

Capital-path on session structural trades: final equity **¥1518700.0**,
PnL **¥18700.0** (CAP rejects: 58,
buying-power rejects: 10).

---

## CSV

`C:/Users/yhach/Documents/tradebotfile/kabu_native/results/reports/phase395_20260615_pm_position_cap_comparison.csv`
