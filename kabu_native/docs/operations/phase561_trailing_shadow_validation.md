# Phase561 — Trailing Shadow Validation

**Verdict:** `phase561_trailing_shadow_validation_done`
**Generated:** 2026-06-27T20:31:26+09:00
**Full period:** 20260529-20260625
**Live window:** 20260616-20260625
**Accepted trades:** 432 (cap=302, live=130)

## Mandatory answers

1. **T2 full period effective?** False (delta=-131730.0)
2. **T2 live window effective?** True (delta=57970.0)
3. **T3 effective?** True (delta=16810.0)
4. **T6 effective?** False (delta=-111720.0)
5. **Best candidate:** T3 (delta=16810.0)
6. **maxDD not worse (T2)?** True
7. **stop_hit not excessive?** True (delta=-17)
8. **Profit days not cut?** False
9. **Dependency not worse?** False (worse_count=6)
10. **Advance runtime candidate?** False []
11. **Next phase:** phase562_exit_observability_refinement

## Improvement day rate

{'T2': 0.25, 'T3': 0.625, 'T6': 0.375}

## Outputs

- `results/reports/phase561_trailing_shadow_summary.csv`
- `results/reports/phase561_trailing_daily.csv`
- `results/reports/phase561_loss_day_impact.csv`
- `results/reports/phase561_profit_day_impact.csv`
- `results/reports/phase561_dependency_audit.csv`
- `results/reports/phase561_report.json`
