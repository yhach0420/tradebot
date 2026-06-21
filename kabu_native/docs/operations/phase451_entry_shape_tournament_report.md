# Phase451 — Entry Shape Filter Tournament

Generated: 2026-06-19T22:30:17+09:00
Verdict: **`uptrend_candidate`**
Period: 20260529..20260619
Best variant (PnL): **F_uptrend_preference**
Practical variant: **E_weak_shape_reject**

## Comparison

| Variant | PnL | ΔPnL | PF | MaxDD | Stop | Acc | OP acc | SOP acc | UP acc | 6/18 Δ | 6/19 Δ | 6976 Δ | 6920 Δ | 4062 Δ |
|---------|-----|------|-----|-------|------|-----|--------|---------|--------|--------|--------|--------|--------|--------|
| A_baseline | 88059.19 | 0.0 | 1.0844 | 148150.0 | 0.1532 | 607 | 113 | 140 | 192 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| B_opening_peak_guard | 109050.82 | 20991.63 | 1.1286 | 109050.0 | 0.1265 | 490 | 22 | 135 | 177 | 43900.0 | -1000.0 | 25001.12 | 0.0 | -27998.85 |
| C_strong_opening_peak | 120511.65 | 32452.46 | 1.1381 | 134550.0 | 0.1252 | 535 | 73 | 112 | 187 | 25100.0 | -2600.0 | 31502.03 | 0.0 | -27998.85 |
| D_no_high_update | -4901.22 | -92960.41 | 0.9921 | 173300.2 | 0.1306 | 337 | 25 | 48 | 133 | 35300.0 | -10150.0 | -15499.43 | 0.0 | -42501.9 |
| E_weak_shape_reject | 122720.94 | 34661.75 | 1.1951 | 116050.0 | 0.0894 | 358 | 0 | 0 | 194 | 47700.0 | -2600.0 | -14999.27 | 0.0 | -27998.85 |
| F_uptrend_preference | 123311.38 | 35252.19 | 2.0991 | 37600.0 | 0.104 | 125 | 29 | 25 | 55 | 61400.0 | 56250.0 | -87498.37 | 0.0 | 18499.43 |
| G_combined_conservative | -14801.5 | -102860.69 | 0.9762 | 178200.2 | 0.1329 | 331 | 22 | 47 | 131 | 35300.0 | -10150.0 | -18499.77 | 0.0 | -42501.9 |
| H_combined_aggressive | 15199.82 | -72859.37 | 1.1721 | 35900.0 | 0.15 | 60 | 4 | 5 | 40 | 56000.0 | 56250.0 | -128499.3 | 0.0 | 498.87 |

## Rankings

- PnL: F_uptrend_preference, E_weak_shape_reject, C_strong_opening_peak, B_opening_peak_guard, A_baseline, H_combined_aggressive, D_no_high_update, G_combined_conservative
- PF: F_uptrend_preference, E_weak_shape_reject, H_combined_aggressive, C_strong_opening_peak, B_opening_peak_guard, A_baseline, D_no_high_update, G_combined_conservative
- MaxDD (lower better): H_combined_aggressive, F_uptrend_preference, B_opening_peak_guard, E_weak_shape_reject, C_strong_opening_peak, A_baseline, D_no_high_update, G_combined_conservative
- Stop rate (lower better): E_weak_shape_reject, F_uptrend_preference, C_strong_opening_peak, B_opening_peak_guard, D_no_high_update, G_combined_conservative, H_combined_aggressive, A_baseline

## Mandatory answers

1. 最良variant: **F_uptrend_preference**
2. PnL順位: ['F_uptrend_preference', 'E_weak_shape_reject', 'C_strong_opening_peak', 'B_opening_peak_guard', 'A_baseline', 'H_combined_aggressive', 'D_no_high_update', 'G_combined_conservative']
3. PF順位: ['F_uptrend_preference', 'E_weak_shape_reject', 'H_combined_aggressive', 'C_strong_opening_peak', 'B_opening_peak_guard', 'A_baseline', 'D_no_high_update', 'G_combined_conservative']
4. maxDD順位: ['H_combined_aggressive', 'F_uptrend_preference', 'B_opening_peak_guard', 'E_weak_shape_reject', 'C_strong_opening_peak', 'A_baseline', 'D_no_high_update', 'G_combined_conservative']
5. stop率順位: ['E_weak_shape_reject', 'F_uptrend_preference', 'C_strong_opening_peak', 'B_opening_peak_guard', 'D_no_high_update', 'G_combined_conservative', 'H_combined_aggressive', 'A_baseline']
6. 6976改善: False
7. 6920改善: False
8. 4062副作用: 18499.43 yen
9. 6/18改善: 61400.0 yen
10. 6/19改善: 56250.0 yen
11. opening_peak削減率: 74.34%
12. uptrend採用率改善: -0.1293
13. Runtime候補: False (recommended shadow: E_weak_shape_reject)

## Notes

- F_uptrend_tradeoff: Highest PnL/PF but 6976 -87k and uptrend adoption -13pp
- E_weak_shape_tradeoff: Near-best PnL (+34.7k), 100% OP/SOP block, PF 1.20, 6976 -15k
- B_opening_peak_tradeoff: 6976 +25k, OP -81%, 6/19 slightly worse
