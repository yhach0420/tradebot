# Phase562 — EXIT Observability Refinement

**Verdict:** `phase562_exit_observability_refinement_done`
**Generated:** 2026-06-27T20:51:59+09:00
**Period:** 20260529-20260625
**Trades analyzed:** 432

## Mandatory answers

1. **T2 effective conditions:** live window (+57,970); AM session (+92,270); loss_day (+28,200); stop_hit exits (+43,870); MFE 2%+ within live (+59,400)
2. **T2 worsen conditions:** cap_extension (-189,700); profit_day cap (-187,700); MFE 2%+ cap (-227,100); board_low cap (-126,900); trailing_mfe cap (-183,500); 73 high-MFE early cuts
3. **T3 effective conditions:** board_high (+16,810); profit_day (+8,610); loss_day (+8,200); MFE 2%+ (+9,310); hold 3-10min (+49,900); dependency improves on exclusions
4. **T3 worsen conditions:** board_low neutral (0); hold 30min+ (-53,190); cap high-MFE winners slightly less captured
5. **board_high/low:** {"T2_board_high": -8400.0, "T2_board_low": -123330.0, "T3_board_high": 16810.0, "T3_board_low": 0.0, "T6_board_high": 16810.0, "T6_board_low": -128530.0}
6. **AM/PM:** {"T2_AM": -54730.0, "T2_PM": -77000.0, "T3_AM": 14910.0, "T3_PM": 1900.0}
7. **MFE buckets:** see report.json
8. **Hold buckets:** see report.json
9. **Runtime candidate:** True — T3 board_high loosen as shadow monitor; T2 live-only watch
10. **Daily Summary metrics:** ['exit_mfe_capture_ratio', 'exit_opportunity_loss_avg', 'exit_early_profit_take_count', 'exit_board_high_trailing_pnl', 'exit_board_low_trailing_pnl', 'shadow_t2_delta', 'shadow_t3_delta']
11. **Shadow monitors:** primary=T3 secondary=T2
12. **Next phase:** phase563_shadow_exit_daily_monitor_pilot

## Outputs

- `results/reports/phase562_exit_segment_effect.csv`
- `results/reports/phase562_t2_effect_profile.csv`
- `results/reports/phase562_t3_effect_profile.csv`
- `results/reports/phase562_exit_monitor_metrics_design.csv`
- `results/reports/phase562_shadow_monitor_design.csv`
- `results/reports/phase562_report.json`
