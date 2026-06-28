# Phase553 — Loss Day Root Cause Analysis (20260618)

**Verdict:** `phase553_loss_day_root_cause_analysis_done`
**Target day:** 20260618
**Variant:** B_current_runtime
**Day PnL:** -50200.0 yen (15 trades)

## Conclusion

20260618 loss driven by concentrated stop_low_mfe PBv2 entries under current runtime filters

15 accepted trades; -50200.0 yen; top3 share 82.22% of day loss; OR trades 0

## Mandatory answers

- **1_max_loss_symbol:** 6976.T
- **2_top3_loss_share_pct:** 82.22
- **3_entry_cause:** Weak Volume
- **4_exit_cause:** stop_hit_dominant
- **5_market_impact:** limited_individual_not_crash
- **6_runtime_preventable_pct:** 92.74
- **7_runtime_not_preventable_pct:** 7.26
- **8_entry_improvement_room:** 58.2
- **9_exit_improvement_room:** 3.2
- **10_universe_improvement_room:** low
- **11_or_improvement_room:** none_or_trades_0
- **12_cap_improvement_room:** low_15_trades_under_cap
- **13_runtime_change_needed:** False
- **14_next_priority:** monitor_entry_quality_on_stop_low_mfe_days
- **day_pnl_yen_100:** -50200.0
- **trade_count:** 15
- **top1_loss_share_pct:** 39.26
- **top5_loss_share_pct:** 92.74
- **responsibility_entry_pct:** 58.2
- **responsibility_exit_pct:** 3.2
- **responsibility_market_pct:** 14.6
- **responsibility_unavoidable_pct:** 24.0

## Output files

- `results/reports/phase553_loss_day_trade_detail.csv`
- `results/reports/phase553_loss_day_ranking.csv`
- `results/reports/phase553_loss_day_entry_analysis.csv`
- `results/reports/phase553_loss_day_exit_analysis.csv`
- `results/reports/phase553_loss_day_market_analysis.csv`
- `results/reports/phase553_loss_day_root_cause.csv`
- `results/reports/phase553_report.json`
