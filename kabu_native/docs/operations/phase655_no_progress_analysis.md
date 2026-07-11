# Phase655: No Progress Entry Quality Analysis

Research-only study on whether `no_progress_exit` trades are predictable at ENTRY or within
30/60/90/120 seconds post-entry, using the Phase634 full-period dataset (18 trading days / 39 sessions).

## Constraints

- No ENTRY / EXIT / PBv2 / OR / YAML / runtime changes
- Research artifacts only

## Run

```bash
cd kabu_native
python scripts/run_phase655_no_progress_entry_quality.py
```

## Dataset

- Loader: `research.phase634_pbv2_only_rise5_full_period.load_all_full_period_trades`
- Cohorts: `no_progress_exit` vs winners (`pnl > 0` or `trailing_mfe_exit`)
- Pools: `all`, `PBV2`, `OR`

## Methods

1. **ENTRY feature comparison** — Cohen's d, mutual information, correlation (no_progress vs winner)
2. **Post-entry horizons** — MFE / MAE / price / board at 30s, 60s, 90s, 120s (price index replay)
3. **Counterfactual early exit** — single / AND / OR rule combos; PnL / PF / maxDD vs baseline
4. **Feature importance** — combined entry + horizon ranking
5. **Leave-one-symbol-out** — symbol dependency check on top separator
6. **Daily** — pre625 vs post625 counterfactual delta

## Outputs

`results/reports/phase655_no_progress_analysis/`

| File | Content |
|------|---------|
| `phase655_report.json` | Verdict + mandatory answers |
| `phase655_feature_importance.csv` | TOP features (entry + horizon) |
| `phase655_time_series.csv` | Horizon metric means + Cohen's d |
| `phase655_counterfactual.csv` | Scenario PnL / PF / DD |
| `phase655_symbol_analysis.csv` | LOO symbol robustness |
| `phase655_daily_analysis.csv` | Per-day + pre/post625 |

## Verdict

`phase655_no_progress_analysis_done`

Final adoption label in report: `ADOPT` / `HOLD` / `REJECT`
