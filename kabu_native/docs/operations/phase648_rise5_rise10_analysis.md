# Phase648: Rise5 × Rise10 Profit Attribution

Research-only analysis of `entry_rise_5min_pct` and `entry_rise_10min_pct` at PBv2 ENTRY.
OR excluded. Uses Phase634 `load_all_full_period_trades`.

## Run

```bash
python scripts/run_phase648_rise5_rise10_analysis.py
python -m pytest tests/test_phase648_rise5_rise10_profit_attribution.py -q
```

## Artifacts

```
results/reports/phase648_rise5_rise10_analysis/
  phase648_report.json
  rise5_distribution.csv
  rise10_distribution.csv
  rise5_rise10_heatmap.csv
  rise_counterfactual.csv
  rise_feature_importance.csv
  sumco_case_study.md
```

## Bands

Rise5/Rise10: `<-3%`, `-3~-2%`, …, `2~3%`, `>3%`

HeatMap axes: Down (`<-0.5%`), Flat (`-0.5~0.5%`), Up (`>0.5%`)

## Counterfactual (block declining rise)

- `rise5_lt_-0.5`, `rise5_lt_-1`, `rise5_lt_-2`
- `rise10_lt_-1`, `rise10_lt_-2`
- `rise5_lt_0_and_rise10_lt_0`
- `rise5_lt_-1_and_rise10_lt_-2`

## Constraints

- No ENTRY / EXIT / PBv2 / OR / YAML changes
- Analysis and counterfactual only

## Verdict

`phase648_rise5_rise10_analysis_done`

## Findings (2026-07-06 run)

- **PBv2 trades:** 3,074 (39 sessions, Phase634 full-period loader)
- **Worst rise5 band:** `0~0.5%` (PnL -290,540) — flat/slight rise, not deep negative
- **Best rise5 band:** `0.5~1%` (PnL +189,930)
- **Deep negative rise** (`-2~-1%`) is profitable (+175k); simple rise5<-1 block **hurts** (ΔPnL -190k)
- **Best counterfactual:** `rise5<0 AND rise10<0` → ΔPnL +61k, ΔDD +232k
- **HeatMap worst cell:** Rise5 Flat × Rise10 Flat (PnL -306k)
- **vs Phase647:** Rise features more directly explain PnL; Phase632 rise5 **upper** cap aligns with 2~3% / >3% loss bands
- **Recommendation:** **HOLD** — joint decline gate is secondary; upper-cap remains primary
