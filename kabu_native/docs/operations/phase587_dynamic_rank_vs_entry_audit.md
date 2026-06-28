# Phase587 — Dynamic Rank vs ENTRY Opportunity Audit

**Verdict:** `phase587_dynamic_rank_vs_entry_audit_done`
**Period:** 20260529–20260626 (41 live sessions)

## Scope

Research-only audit: whether Dynamic rank selects symbols with more ENTRY opportunities.
No Runtime / Universe / ENTRY changes.

## Pipeline split

```
Ranking → Universe visibility → ENTRY eval → ENTRY candidate → ENTRY accept → EXIT
```

- **ENTRY candidate**: eval excluding `or_overlay_not_candidate` / outside window.
- **candidate_rate**: candidates / eval_count (~98% flat across ranks).
- **accept_rate**: accepted / candidates (~0.3–0.5%).

## Mandatory answers

1. Rank ↔ ENTRY candidate rate: **No** (Pearson=0.0526, Spearman=0.0428)
2. Rank ↔ ENTRY accept rate: **No** (Pearson=0.0029, Spearman=-0.0668)
3. Rank ↔ profit rate: **Yes (weak negative)** (Spearman=-0.1816)
4. High rank → more ENTRY candidates: **No (flat; rank31-35 highest)** (rank1-5=2800.52/day vs rank31-35=2980.14/day)
5. Low-rank high-candidate symbols exist: **Yes** (16 symbols)
6. Universe Ranking = ENTRY opportunity ranking: **No**
7. Universe Ranking improvement needed (for ENTRY): **No** — rank does not order ENTRY opportunity
8. Primary bottleneck is ENTRY gates: **Yes** (eval→accept ~0.3973%; candidate→accept drop ~99.6%)
9. Runtime change candidate from this audit: **No**
10. Next phase: **phase588_entry_gate_attribution_research**

## Investigation 9 — What ranking optimizes

**Conclusion:** liquidity_and_volatility_not_entry_opportunity_or_profit

| Hypothesis | Supported |
|---|---|
| Liquidity × volatility (AM score) | Yes |
| ENTRY opportunity density | No |
| ENTRY acceptance quality | No |
| Profit expectancy | Weak negative only |

## Funnel insight (all ranks similar)

- Global candidate rate: 98.44% of evals
- Global accept rate: 0.3973% of evals
- Largest drop: **candidate → accept** (ENTRY gates), not eval → candidate

## Top gate blockers (candidate rejects)

- rank_1_5 / board: 125968
- rank_6_10 / board: 116480
- rank_16_20 / board: 99188
- rank_31_35 / board: 98469
- rank_11_15 / board: 93021
- rank_21_25 / board: 87803
- rank_36_40 / volume: 79014
- rank_36_40 / board: 78872

## Outputs

- `results/reports/phase587_rank_entry_candidate.csv`
- `results/reports/phase587_rank_entry_funnel.csv`
- `results/reports/phase587_rank_entry_correlation.csv`
- `results/reports/phase587_rank_high_low_outliers.csv`
- `results/reports/phase587_ranking_role_summary.csv`
- `results/reports/phase587_rank_entry_gate_breakdown.csv`
- `results/reports/phase587_report.json`