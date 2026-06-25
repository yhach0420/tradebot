# Phase492 — 20260622 PM Entry Failure Audit

**Verdict:** `entry_quality_problem`

## Session

- Day: 20260622 | Session: `live_session_122529`
- Canonical PF: **0.3232**
- Failures (stop+NP): **30** | Winners (trailing): **11**

## 必須回答

1. **PM崩壊の主因:** stop_hit(10) + no_progress(20) = 30 trades / -67,000 yen; PF=0.3232
2. **6522/6976/6981:** {'6522': 'falling_knife_entry (直前下落中エントリー)', '6976': 'mixed / unclear', '6981': 'mixed / unclear'}
3. **late_chase仮説:** CONFIRMED (phase483_trap_match=47%, late_chase_cluster=0%)
4. **実装価値:** Moderate — counterfactual C improves PF but blocks winners; guard tuning replay required
5. **過学習リスク:** High if deployed on single-day PM slice; Phase483 pattern is population-level, 6522 concentration (5 stops) is symbol-specific

## Feature diff (failures vs trailing winners)

```json
{
  "r5": 0.2893,
  "r10": 0.6009,
  "r15": null,
  "r30": null,
  "vwap_dev_pct": 0.6721,
  "momentum_continuation_score": 0.0295,
  "day_high_distance_pct": null,
  "r30_minus_r5": 0.3156,
  "vwap_extension_rate": 0.6721,
  "pre_rally_5m": 0.2893,
  "pre_rally_30m": null
}
```

## Counterfactual

```json
{
  "A": {
    "scenario": "A_r30_minus_r5_top20",
    "threshold_pct": 80,
    "metric": "r30_minus_r5",
    "threshold_value": 0.4566,
    "blocked_total": 9,
    "blocked_winners": 3,
    "blocked_losers": 6,
    "blocked_flat": 0,
    "blocked_pnl_yen_100": -16500.0,
    "remaining_pnl_yen_100": -41200.0,
    "delta_pnl_yen_100": 16500.0,
    "remaining_pf": 0.3607,
    "baseline_pnl_yen_100": -57700.0,
    "baseline_pf": 0.3232
  },
  "B": {
    "scenario": "B_vwap_extension_top20",
    "threshold_pct": 80,
    "metric": "vwap_extension_rate",
    "threshold_value": 2.3437,
    "blocked_total": 10,
    "blocked_winners": 3,
    "blocked_losers": 7,
    "blocked_flat": 0,
    "blocked_pnl_yen_100": -5700.0,
    "remaining_pnl_yen_100": -52000.0,
    "delta_pnl_yen_100": 5700.0,
    "remaining_pf": 0.2997,
    "baseline_pnl_yen_100": -57700.0,
    "baseline_pf": 0.3232
  },
  "C": {
    "scenario": "C_A_plus_B_top20",
    "threshold_pct": 80,
    "metric": "r30_minus_r5 AND vwap_extension_rate",
    "threshold_value_a": 0.4566,
    "threshold_value_b": 2.3437,
    "blocked_total": 4,
    "blocked_winners": 1,
    "blocked_losers": 3,
    "blocked_flat": 0,
    "blocked_pnl_yen_100": -4400.0,
    "remaining_pnl_yen_100": -53300.0,
    "delta_pnl_yen_100": 4400.0,
    "remaining_pf": 0.3317,
    "baseline_pnl_yen_100": -57700.0,
    "baseline_pf": 0.3232
  }
}
```

## Outputs

- `phase492_20260622_pm_entry_failure_audit.csv`
- `phase492_20260622_pm_counterfactual.csv`
- `phase492_20260622_pm_symbol_review.csv`
- `phase492_summary.json`
