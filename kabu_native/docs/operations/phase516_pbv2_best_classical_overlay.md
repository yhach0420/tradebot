# Phase516 — PBv2 + Best Classical Entry Overlay

**Verdict:** `phase516_pbv2_best_classical_overlay_done`
**Period:** 20260529 – 20260622
**Workers:** 4

## Summary

| Scenario | Mode | PnL | PF | maxDD | Trades | ΔPnL |
|----------|------|-----|----|-------|--------|------|
| BASELINE | - | 214959.61 | 1.3476 | 118600.0 | 440 | 0.0 |
| O_D506_AND | AND | 11500.0 | inf | 0.0 | 2 | -203459.61 |
| O_D506_OR | OR | 199410.8 | 1.3201 | 60098.94 | 408 | -15548.81 |
| O_R003_AND | AND | 11500.0 | inf | 0.0 | 2 | -203459.61 |
| O_R003_OR | OR | 547110.65 | 1.8533 | 67099.41 | 437 | 332151.04 |
| O_M002_AND | AND | 32469.95 | 1.228 | 87000.0 | 70 | -182489.66 |
| O_M002_OR | OR | -82808.31 | 0.9214 | 205799.62 | 722 | -297767.92 |

## Attribution

**O_D506_AND**: pbv2=2, overlay=2, both=2, overlay_only=0, pbv2_only=0, prevented_loss=618389.01, lost_profit=824449.47, substitution_profit=0
**O_D506_OR**: pbv2=268, overlay=142, both=2, overlay_only=140, pbv2_only=266, prevented_loss=237850.71, lost_profit=299599.52, substitution_profit=41200.0
**O_R003_AND**: pbv2=2, overlay=2, both=2, overlay_only=0, pbv2_only=0, prevented_loss=618389.01, lost_profit=824449.47, substitution_profit=0
**O_R003_OR**: pbv2=265, overlay=174, both=2, overlay_only=172, pbv2_only=263, prevented_loss=237850.74, lost_profit=254499.7, substitution_profit=343800.0
**O_M002_AND**: pbv2=70, overlay=70, both=70, overlay_only=0, pbv2_only=0, prevented_loss=558189.12, lost_profit=735179.96, substitution_profit=0
**O_M002_OR**: pbv2=118, overlay=610, both=6, overlay_only=604, pbv2_only=112, prevented_loss=439789.74, lost_profit=593167.66, substitution_profit=-149390.0

## Overfit audit (top candidates)

**O_R003_OR**: top1_trade=21.59%, top10_trade=50.45%, top1_sym=71.92%, top3_sym=90.75%, top1_day=38.4%, top3_day=76.64%
**O_D506_OR**: top1_trade=8.03%, top10_trade=39.8%, top1_sym=38.36%, top3_sym=83.49%, top1_day=44.58%, top3_day=99.04%
**O_M002_AND**: top1_trade=40.89%, top10_trade=81.49%, top1_sym=70.83%, top3_sym=114.26%, top1_day=189.71%, top3_day=360.64%

## Mandatory answers

1. Beats PBv2 (PnL+PF+DD): **['O_R003_OR']**
2. PnL improvement: **['O_R003_OR']**
3. PF improvement: **['O_D506_AND', 'O_R003_AND', 'O_R003_OR']**
4. DD improvement: **['O_D506_AND', 'O_D506_OR', 'O_R003_AND', 'O_R003_OR', 'O_M002_AND']**
5. Best AND: **O_M002_AND** (PnL 32469.95)
6. Best OR: **O_R003_OR** (PnL 547110.65)
7. D506 contributes: **False** (AND ΔPnL -203459.61, OR ΔPnL -15548.81)
8. R003 contributes: **True** (AND ΔPnL -203459.61, OR ΔPnL 332151.04)
9. M002 contributes: **False** (AND ΔPnL -182489.66, OR ΔPnL -297767.92)
10. Overlay has value: **True**
11. Adoption candidates: **['O_R003_OR']** (adopt_not_allowed=True)
