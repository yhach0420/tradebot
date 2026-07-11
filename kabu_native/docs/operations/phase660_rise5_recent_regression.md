# Phase660: Rise5 Recent Regression Root Cause

## Purpose

Explain why rise5 shadow ΔPnL was **+112,250** over 22 days but **-4,300** over the
recent 5 trading days (Phase659). Research only.

## Run

```bash
python scripts/run_phase660_rise5_recent_regression.py
```

## Outputs

`results/reports/phase660_rise5_recent_regression/`

- `phase660_report.json`
- `phase660_daily_comparison.csv`
- `phase660_symbol_analysis.csv`
- `phase660_market_regime.csv`
- `phase660_threshold_sweep.csv`
- `phase660_overlap_analysis.csv`

## Verdict

`phase660_rise5_recent_regression_done`
