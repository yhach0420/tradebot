# Phase541 — Guard v2 Full-Period Validation

**Verdict:** `phase541_guard_v2_full_period_validation_done`
**Period:** 20260616 – 20260625 (all sessions)
**Trades:** 1309

## Mandatory answers

- **1. G3 effective full period?** True
- **2. G11 effective full period?** True
- **3. G12 effective full period?** False
- **4. G12 overfit risk?** True
- **5. Best guard** G13_adx_five_min
- **6. Most explainable guard** G13_adx_five_min
- **7. MFE0 primary loss driver?** True
- **8. NoProgress primary loss driver?** False
- **9. Winner over-block?** True
- **10. Shadow forward ready?** True
- **11. Production adoption candidate?** False
- **12. Next phase** Phase542: forward-shadow top guard(s) on new live days before Runtime wiring.

## Guards tested

- A: baseline
- G3: ADX14 <= 30
- G11: five_min_position <= 33.3333
- G12: five_min_position <= 33.3333 AND moving_average_position <= 0.1314
- G13: ADX14 <= 30 AND five_min_position <= 33.3333
- G14: ADX14 <= 30 AND moving_average_position <= 0.1314

## Outputs

- `results/reports/phase541_guard_v2_full_period_summary.csv`
- `results/reports/phase541_guard_v2_daily.csv`
- `results/reports/phase541_guard_v2_blocked_trades.csv`
- `results/reports/phase541_guard_v2_mfe0_reduction.csv`
- `results/reports/phase541_guard_v2_dependency.csv`
- `results/reports/phase541_feature_quality.csv`
- `results/reports/phase541_report.json`
