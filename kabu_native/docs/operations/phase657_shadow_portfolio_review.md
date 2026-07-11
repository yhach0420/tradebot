# Phase657: Shadow Portfolio Final Review

Research-only portfolio review across Runtime ENTRY/EXIT shadows, Forward shadows,
and Research counterfactuals. Produces ADOPT / KEEP / MERGE / REMOVE for each shadow.

## Constraints

- No ENTRY / EXIT / PBv2 / OR / YAML / runtime changes
- No new shadows — consolidation and decisions only

## Run

```bash
cd kabu_native
python scripts/run_phase657_shadow_portfolio_review.py
```

## Data sources

- Phase652 shadow registry + session `small_paper_summary.json` (and AM/PM fallbacks)
- Phase654 / 655 / 656 research reports (when present)
- 22 trading days / 41 sessions (Phase634 replayable universe)

## Scoring (100 points)

| Dimension | Weight |
|-----------|--------|
| Expected value improvement | 25 |
| Stability (day-level) | 20 |
| Reproducibility (pre625/post625) | 15 |
| Runtime CPU load (inverse) | 10 |
| Maintainability / Discord | 10 |
| Side effects (blocked winners) | 10 |
| Adopt ease | 10 |

## Outputs

`results/reports/phase657_shadow_portfolio_review/`

| File | Content |
|------|---------|
| `phase657_report.json` | Verdict + mandatory answers 1-12 |
| `phase657_shadow_scorecard.csv` | Per-shadow scores + decision |
| `phase657_shadow_ranking.csv` | Ranked by total_score |
| `phase657_adopt_keep_remove.csv` | Final decisions |
| `phase657_runtime_overview.csv` | Runtime shadows only |
| `phase657_forward_overview.csv` | Forward shadows only |
| `phase657_architecture.md` | Post-review architecture diagram |

## Verdict

`phase657_shadow_portfolio_review_done`
