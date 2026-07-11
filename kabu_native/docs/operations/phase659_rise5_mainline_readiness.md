# Phase659: Rise5 Mainline Promotion Readiness

## Purpose

Final readiness review for promoting `pbv2_rise5_shadow` to mainline ENTRY guard
after Phase658 ADOPT candidacy. Analysis only — no ENTRY/EXIT/YAML/runtime changes.

## Run

```bash
python scripts/run_phase659_rise5_mainline_readiness.py
```

## Dataset

Phase634 full-period PBv2 trades (~41 sessions / 22 trading days).

Counterfactual: block PBv2 accepted when `entry_rise_5min_pct > 1.84` (phase635 threshold).

## Outputs

`results/reports/phase659_rise5_mainline_readiness/`

- `phase659_report.json`
- `phase659_daily_breakdown.csv`
- `phase659_leave_one_day_out.csv`
- `phase659_leave_one_symbol_out.csv`
- `phase659_risk_review.csv`

## Verdict

`phase659_rise5_mainline_readiness_done`

Final promotion verdict in report: `ADOPT` | `HOLD` | `REJECT`
