# Phase656: Winner Attribution and Entry Quality Upgrade

Research-only analysis of **big-winner** trade characteristics on the Phase634 full-period dataset,
with ENTRY-quality counterfactual filters. No runtime / YAML / trading logic changes.

## Dataset

- Phase634 loader: 41 sessions / 22 days / 3,192 trades (as of latest replay)
- Primary pool: **PBv2** (OR reported separately)
- PnL buckets: big_winner (top 10%), mid_winner (10-30%), neutral (30-70%), loser (10-30% bottom), big_loser (bottom 10%)

## Run

```bash
cd kabu_native
python scripts/run_phase656_winner_attribution.py
```

## Analyses

1. **Big winner vs loser / no_progress / stop_hit** — ENTRY + EXIT feature distributions
2. **Feature importance** — Cohen's d, mutual information, correlation, permutation importance, threshold lift
3. **Condition candidates** — big-winner-favor / loser-avoid profiles from percentile thresholds
4. **Counterfactual variants**
   - A: Big Winner Favor
   - B: Loser Avoid
   - C: Favor AND NOT flat-band
   - D: Favor AND low price_age
   - E: Favor AND board/momentum
5. **Robustness** — daily, AM/PM, symbol, leave-one-symbol-out, pre625/post625

## Outputs

`results/reports/phase656_winner_attribution/`

| File | Content |
|------|---------|
| `phase656_report.json` | Verdict + mandatory answers |
| `phase656_feature_importance.csv` | Feature ranking |
| `phase656_distribution_comparison.csv` | big_winner vs comparison groups |
| `phase656_counterfactual.csv` | Variant PnL / PF / DD |
| `phase656_daily_breakdown.csv` | Per-day deltas |
| `phase656_symbol_breakdown.csv` | Per-symbol impact |
| `phase656_leave_one_symbol_out.csv` | LOO robustness |

## Verdict

`phase656_winner_attribution_done`

Final adoption label: `ADOPT` / `HOLD` / `REJECT` in `mandatory_answers.9_final_verdict`
