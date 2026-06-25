# Phase517 — O_R003_OR Robustness Audit

**Verdict:** `phase517_o_r003_or_robustness_audit_done`
**Period:** 20260529 – 20260622

## Investigation 1: Overall comparison

| Scenario | PnL | PF | maxDD | Trades | ΔPnL |
|----------|-----|----|-------|--------|------|
| BASELINE | 214959.61 | 1.3476 | 118600.0 | 440 | 0.0 |
| O_R003_OR | 547110.65 | 1.8533 | 67099.41 | 437 | 332151.04 |
| O_D506_OR | 199410.8 | 1.3201 | 60098.94 | 408 | -15548.81 |

## Investigation 2: Exclusion robustness (O_R003_OR)

- **symbol_6976**: PnL=153612.12, PF=1.2585, DD=79299.65, beats_baseline=False, positive=True
- **top1_symbol**: PnL=153612.12, PF=1.2585, DD=79299.65, beats_baseline=False, positive=True
- **top3_symbols**: PnL=50613.11, PF=1.0866, DD=114096.82, beats_baseline=False, positive=True
- **day_20260615**: PnL=337010.06, PF=1.6154, DD=60098.94, beats_baseline=True, positive=True
- **top1_day**: PnL=337010.06, PF=1.6154, DD=60098.94, beats_baseline=True, positive=True
- **top3_days**: PnL=127811.16, PF=1.271, DD=66696.92, beats_baseline=False, positive=True
- **top10_trades**: PnL=-52389.27, PF=0.9183, DD=132097.64, beats_baseline=False, positive=False

## Investigation 3: Attribution

- **overlay_only**: trades=172, PnL=350800.0, contribution=64.12%
- **pbv2_only**: trades=263, PnL=187411.5, contribution=34.25%
- **replaced_by_cap**: trades=176, PnL=16648.96, contribution=3.04%
- **both**: trades=2, PnL=8899.15, contribution=1.63%
- **skipped_by_cap**: trades=415, PnL=-243361.04, contribution=-44.48%

## Investigation 4: CAP collision

- cap_block_count: 282
- pbv2 lost: 176 trades / 16648.96 yen
- overlay added: 173 trades / 343800.0 yen
- net_substitution_pnl: 360448.96

## Investigation 7: D506_OR vs R003_OR

- R003 PnL: 547110.65 vs D506 PnL: 199410.8
- R003 6976 share: 71.92% vs D506: 38.36%
- Reason: stricter updates<=6 + ADX>=15 reduces overlay candidate count and substitution profit

## Mandatory answers

1. Robust or fragile: **fragile**
2. Beats baseline after 6976 exclusion: **False**
3. Beats baseline after top3 symbol exclusion: **False**
4. Beats baseline after 20260615 exclusion: **True**
5. Beats baseline after top3 day exclusion: **False**
6. Improvement from overlay_only: **True** (PnL 350800.0)
7. PBv2 core maintained: **True**
8. CAP collision worsened: **True**; net substitution positive: **True**
9. Overlay-only separate edge: **True**
10. R003 stronger than D506: **{'r003_pnl_delta_vs_baseline': 332151.04, 'd506_pnl_delta_vs_baseline': -15548.81, 'r003_overlay_only_trades': 172, 'd506_more_restrictive': 'updates<=6 AND ADX>=15 vs updates<=8', 'd506_better_dispersion': True}**
11. Shadow candidate worth: **False**
12. Production adopt OK: **False** (adopt_not_allowed=True)
