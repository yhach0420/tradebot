# Phase585 — Dynamic Ranking Quality Audit

**Verdict:** `phase585_dynamic_ranking_quality_audit_done`
**Period:** 20260529–20260626

## Mandatory answers

1. Rank expectancy-ordered: False
2. Rank1-25 strong: True
3. Rank26-40 weak: True
4. Weak symbols in top25: True (58)
5. Strong symbols in lower ranks: True (22)
6. Ranking score correlates: False
7. Effective component: liquidity_score
8. Ineffective component: rank
9. Replacement improves: True (delta=437400.0)
10. Dynamic25 production candidate: False
11. Core not required: True
12. Runtime change candidate: False
13. Next phase: phase586_ranking_score_improvement_research

## Monotonicity

| metric | Pearson | Spearman | matches |
|--------|---------|----------|---------|
| pnl_yen_100 | -0.0522 | -0.0623 | True |
| profit_factor | -0.0557 | -0.0912 | True |
| avg_pnl_yen_100 | -0.161 | -0.0865 | True |
| stop_low_mfe_count | -0.107 | -0.0639 | False |
| mfe_avg | -0.159 | -0.2296 | True |