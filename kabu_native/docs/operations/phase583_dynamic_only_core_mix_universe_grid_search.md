# Phase583 — Dynamic Only / Core Mix Universe Grid Search

**Verdict:** `phase583_dynamic_only_core_mix_universe_grid_search_done`
**Period:** 20260529–20260626
**Grid size:** 38 configurations

## Mandatory answers

1. Best universe: C5_D25 (Core5+Dynamic25) score=1.58268
2. Best Dynamic only: C0_D25 PnL=203700.0 PF=1.0907
3. Best Core mix: C5_D25 PnL=232110.0 PF=1.0904
4. Core necessary: True
5. Core10 advantageous: False
6. Optimal dynamic count: 25
7. Best total≤50: C5_D25 PnL=232110.0 PF=1.0904
8. Best total≤60: C5_D25 PnL=232110.0 PF=1.0904
9. Max PnL config: C5_D25 PnL=232110.0
10. Max PF config: C0_D10 PF=1.1129
11. Max stability config: C0_D55 stability=0.4823
12. Lowest dependency config: C5_D25 Top3=160.31%
13. Delta vs baseline — PnL: 94220.0, PF: 0.0622
14. Runtime change candidate: True ['C5_D25', 'C5_D20', 'C5_D15', 'C0_D25', 'C5_D40']
15. Next phase: phase584_universe_shadow_adoption_review

## Top 10 by composite score

| Rank | ID | Label | Total | PnL | PF | MaxDD |
|------|-----|-------|-------|-----|-----|-------|
| 1 | C5_D25 | Core5+Dynamic25 | 30 | 232110.0 | 1.0904 | 2791910.0 |
| 2 | C5_D20 | Core5+Dynamic20 | 25 | 212310.0 | 1.0919 | 1026630.0 |
| 3 | C5_D15 | Core5+Dynamic15 | 20 | 202210.0 | 1.1041 | 6786320.0 |
| 4 | C0_D25 | Dynamic25_only | 25 | 203700.0 | 1.0907 | 2667200.0 |
| 5 | C5_D40 | Core5+Dynamic40 | 45 | 209310.0 | 1.0463 | 3638560.0 |
| 6 | C5_D45 | Core5+Dynamic45 | 50 | 202310.0 | 1.0437 | 4244610.0 |
| 7 | C0_D55 | Dynamic55_only | 55 | 191600.0 | 1.0427 | 3173000.0 |
| 8 | C0_D20 | Dynamic20_only | 20 | 183900.0 | 1.0925 | 476600.0 |
| 9 | C0_D45 | Dynamic45_only | 45 | 187000.0 | 1.0432 | 4539500.0 |
| 10 | C0_D15 | Dynamic15_only | 15 | 173800.0 | 1.1074 | 5080000.0 |