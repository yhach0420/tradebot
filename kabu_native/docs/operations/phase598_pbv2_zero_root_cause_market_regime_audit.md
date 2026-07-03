# Phase598 PBv2 Zero Root Cause + Market Regime Audit

**Verdict:** `phase598_pbv2_zero_root_cause_market_regime_audit_done`
**Classification:** `B_partial_or_fallback_dominant_single_day_not_market_outlier`

## Mandatory answers

1. 20260629 AM first 100% OR_OVERLAY day; OR overlay live since 20260625
2. No — 20260623 PM also zero accepts; pbv2=0+accept>0 only 20260629 AM
3. Low — regime metrics within normal band vs 20260624/25
4. No — OR runs after PBv2 reject only
5. Partial on 20260629 AM — sole accept path, not a code bypass
6. quality_below_0.7 (~82% of fresh/or rows)
7. Dominant filter; 0.65 adds ~4-5pp virtual passes (investigation only)
8. Secondary (~30% board_true on fresh); not primary vs quality
9. Filters high-momentum (~10%); PBv2 requires low momentum by design
10. No — events are source of truth; summary pbv2_count undercounts pre/post edge cases
11. No — optional observability only
12. True
13. phase599_or_overlay_accept_attribution_and_pbv2_near_miss_monitor

## Outputs

- `phase598_daily_pbv2_or_accept_trend.csv`
- `phase598_phase_boundary_comparison.csv`
- `phase598_20260629_pbv2_reject_detail.csv`
- `phase598_market_regime_20260629.csv`
- `phase598_pbv2_zero_day_comparison.csv`
- `phase598_split_cap_interaction_audit.csv`
- `phase598_pbv2_threshold_sensitivity.csv`
- `phase598_report.json`