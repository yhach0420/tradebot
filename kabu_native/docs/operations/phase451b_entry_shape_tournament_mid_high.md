# Phase451B — Entry Shape Tournament (Board Mid+High)

Generated: 2026-06-19T22:41:59+09:00
Board verdict: **`board_high_superior`**
Shape verdict: **`weak_shape_candidate`**
Period: 20260529..20260619

## Population

- Mid-only candidates: 694
- Mid+High candidates: 724
- **Board:high added: 30**
- Board:high exclusive: 30

## Board cohort (HD+NP, no shape guard)

| Cohort | Candidates | Accepted | PnL | PF |
|--------|------------|----------|-----|-----|
| Board:mid only | 694 | 609 | 122310.63 | 1.1233 |
| Board:high only | 30 | 30 | 4310.66 | 1.2318 |

Phase451 mid baseline reference PnL: 88059.19
451B mid+high baseline PnL: 126621.29 (Δ 38562.1)

## Tournament

| Variant | PnL | ΔPnL | PF | MaxDD | Acc | 6/18 Δ | 6/19 Δ | 6976 Δ | 4062 Δ |
|---------|-----|------|-----|-------|-----|--------|--------|--------|--------|
| A_baseline_mid_high | 126621.29 | 0.0 | 1.1253 | 160050.89 | 639 | 0.0 | 0.0 | 0.0 | 0.0 |
| B_opening_peak_guard | 108551.01 | -18070.28 | 1.1256 | 116050.0 | 506 | 43900.0 | -5400.0 | -5500.5 | 0.0 |
| C_strong_opening_peak | 123622.1 | -2999.19 | 1.1402 | 134550.0 | 555 | 25100.0 | 0.0 | 1000.41 | 0.0 |
| D_no_high_update | -2201.22 | -128822.51 | 0.9965 | 173300.2 | 343 | 35300.0 | -7550.0 | -46001.05 | -14503.05 |
| E_weak_shape_reject | 134531.13 | 7909.84 | 1.2105 | 116050.0 | 378 | 47700.0 | 0.0 | -45500.89 | 0.0 |
| F_uptrend_preference | 122311.61 | -4309.68 | 2.0665 | 37600.0 | 131 | 61400.0 | 58850.0 | -117999.99 | 46498.28 |
| G_combined_conservative | -12101.5 | -138722.79 | 0.9807 | 178200.2 | 337 | 35300.0 | -7550.0 | -49001.39 | -14503.05 |
| H_combined_aggressive | 13299.82 | -113321.47 | 1.1474 | 35900.0 | 61 | 56000.0 | 58850.0 | -159000.92 | 28497.72 |

## Mandatory answers

1. Board:high追加候補: **30**
2. Board:high単独PF: 1.2318
3. Board:high単独PnL: 4310.66 yen
4. Mid vs High: mid PnL=122310.63 PF=1.1233 / high PnL=4310.66 PF=1.2318
5. 最良variant: **E_weak_shape_reject**
6. PnL順位: ['E_weak_shape_reject', 'A_baseline_mid_high', 'C_strong_opening_peak', 'F_uptrend_preference', 'B_opening_peak_guard', 'H_combined_aggressive', 'D_no_high_update', 'G_combined_conservative']
7. PF順位: ['F_uptrend_preference', 'E_weak_shape_reject', 'H_combined_aggressive', 'C_strong_opening_peak', 'B_opening_peak_guard', 'A_baseline_mid_high', 'D_no_high_update', 'G_combined_conservative']
8. maxDD順位: ['H_combined_aggressive', 'F_uptrend_preference', 'B_opening_peak_guard', 'E_weak_shape_reject', 'C_strong_opening_peak', 'A_baseline_mid_high', 'D_no_high_update', 'G_combined_conservative']
9. 6976影響: -45500.89 yen
10. 4062影響: 0.0 yen
11. 6/18影響: 47700.0 yen
12. 6/19影響: 0.0 yen
13. Runtime候補: True (recommended: E_weak_shape_reject)
