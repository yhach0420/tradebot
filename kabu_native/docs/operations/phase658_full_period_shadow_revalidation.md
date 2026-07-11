# Phase658: Full-period Shadow Revalidation

## Purpose

Revalidate all Phase657 shadows on the **same Phase634 universe**
(~41 sessions / 22 trading days / ~3,192 trades) with unified metrics.
No ENTRY/EXIT/PBv2/OR/YAML changes; analysis only.

## Run

```bash
python scripts/run_phase658_full_period_shadow_revalidation.py
```

Slow path (includes Phase655 horizon replay, ~10+ min):

```bash
python scripts/run_phase658_full_period_shadow_revalidation.py
# default includes phase655 when not using --skip-slow
```

Fast path (skip Phase655 horizon enrichment):

```bash
python scripts/run_phase658_full_period_shadow_revalidation.py --skip-slow
```

## Evaluation lanes

| Lane | Method | Shadows |
|------|--------|---------|
| Trade replay (entry block) | Counterfactual keep/drop on trades | rise5, flat_band, pullback, vwap, board_imbalance, limit_up |
| Trade event (exit overlay) | Per-trade shadow PnL / delta fields | board_dynamic, T2/T3, realtime_board |
| Session summary | Sum nested summary deltas in universe | volume_gate, forward shadows, extension finalize |
| Research replay | Phase632/633/634/647/649/656 counterfactuals | research shadows |
| Unevaluable | Documented reason | phase643, phase651, phase655 (if skip_slow) |

## Outputs

`results/reports/phase658_full_period_shadow_revalidation/`

- `phase658_report.json`
- `phase658_shadow_revalidation_summary.csv`
- `phase658_shadow_daily_breakdown.csv`
- `phase658_shadow_symbol_breakdown.csv`
- `phase658_shadow_evaluation_gaps.csv`
- `phase658_adopt_keep_remove_revised.csv`

## Verdict

`phase658_full_period_shadow_revalidation_done`
