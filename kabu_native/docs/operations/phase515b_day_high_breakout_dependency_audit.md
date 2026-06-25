# Phase515B — day_high Breakout Dependency Audit

**Verdict:** `phase515b_day_high_breakout_dependency_audit_done`

## Dependency summary

| Strategy | PnL | PF | top1 sym% | top1 day% | Verdict |
|----------|-----|-----|-----------|-----------|---------|
| BASELINE_RUNTIME | 214959.61 | 1.3476 | 67.92 | 44.15 | classic_candidate_fragile |
| P515A_B_005 | 455150.0 | 1.9735 | 79.31 | 56.36 | classic_candidate_fragile |
| P515A_M_002 | 387800.0 | 1.4638 | 107.79 | 70.4 | classic_candidate_fragile |

## Mandatory answers

1. B005 robust/fragile: **classic_candidate_fragile**
2. Beats PBv2 after 6976 exclusion: **False**
3. Positive after top3 symbol exclusion: **False**
4. Positive after top3 day exclusion: **True**
5. true_breakout ratio: **0.5538**
6. high_chase ratio: **0.0699**
7. Winner traits: **{'median_minutes_from_open': 111.95, 'median_first_breakout_pct': 0.0474, 'high_update_continues_pct': 0.9763}**
8. Loser traits: **{'median_minutes_from_open': 108.38, 'late_breakout_pct': 0.8075, 'high_chase_class_pct': 0.1491}**
9. Same edge as PBv2: **False**
10. Deep dive worthy: **True**
