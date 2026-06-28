# Phase582 — Universe Optimization Study

**Verdict:** `phase582_universe_optimization_study_done`
**Period:** 20260529–20260626
**Baseline trades (C):** 2755

## Mandatory answers

1. Best universe: E (Core5+Dynamic40)
2. Best PF: A PF=1.0527
3. Best PnL: E PnL=209310.0
4. Delta vs Current40 — PnL: 71420.0, PF: 0.0181
5. Dynamic40 too many: True
6. Dynamic20 too few: False
7. Core10 optimal: False
8. High-price dependency improves with best: True
9. Symbol dependency improves with best: True
10. Universe change worth it: True
11. Runtime change needed: False
12. Next phase: phase583_universe_shadow_adoption_review

## Universe summary

| ID | Label | Trades | PnL | PF | MaxDD | Top3% |
|----|-------|--------|-----|----|----|-------|
| A | Core10+Dynamic20 | 1866 | 140890.0 | 1.0527 | 3139960.0 | 264.11 |
| B | Core10+Dynamic30 | 2335 | 84390.0 | 1.0246 | 11533460.0 | 353.0 |
| C | Core10+Dynamic40 | 2755 | 137890.0 | 1.0282 | 12452370.0 | 320.76 |
| D | Core10+Dynamic50 | 2757 | 130390.0 | 1.0267 | 14414720.0 | 339.21 |
| E | Core5+Dynamic40 | 2407 | 209310.0 | 1.0463 | 3638560.0 | 211.31 |
| F | Core15+Dynamic35 | 2589 | 40290.0 | 1.0093 | 36708640.0 | 770.41 |
| G | Dynamic50_only | 2225 | 148100.0 | 1.0335 | 8645550.0 | 298.65 |
| H | Core10_only | 687 | -43010.0 | 0.9374 | 13364940.0 | -152.52 |

## Dynamic size curve (A→D)

- PF monotonic up: False
- PF monotonic down: False
- Dynamic40 dead/low symbols: 217