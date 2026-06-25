# Phase514 — PBv2 Overlay Attribution

**Verdict:** `phase514_pbv2_overlay_attribution_done`
**Period:** 20260529 – 20260622

## Summary

| Scenario | PnL | PF | maxDD | Trades | ΔPnL vs BASE |
|----------|-----|----|-------|--------|--------------|
| BASELINE | 214959.61 | 1.3476 | 118600.0 | 440 | 0.0 |
| O1 | 43079.56 | 2.0187 | 24800.04 | 57 | -171880.05 |
| O2 | 23269.94 | 1.1632 | 88400.0 | 68 | -191689.67 |
| O3 | 161680.04 | 2.6687 | 35100.0 | 101 | -53279.57 |
| O4 | 87981.34 | 1.6132 | 57498.69 | 130 | -126978.27 |
| O5 | 159479.92 | 2.5637 | 24390.28 | 135 | -55479.69 |

## Attribution (vs BASELINE)

**O1**: adopted=57, excluded=383, substitution=0, prevented_losses=584148.5, lost_gains=722579.91
**O2**: adopted=68, excluded=372, substitution=0, prevented_losses=555089.15, lost_gains=743879.71
**O3**: adopted=101, excluded=339, substitution=0, prevented_losses=524649.01, lost_gains=610580.24
**O4**: adopted=130, excluded=310, substitution=0, prevented_losses=468249.53, lost_gains=588181.78
**O5**: adopted=135, excluded=305, substitution=0, prevented_losses=514148.35, lost_gains=648679.78

## Mandatory answers

1. PnL improvement candidates: **[]**
2. PF improvement candidates: **['O1', 'O3', 'O4', 'O5']**
3. DD improvement candidates: **['O1', 'O2', 'O3', 'O4', 'O5']**
4. Best overlay: **O3** (PBv2 + VWAP Filter)
5. T13 (O1) effective: **{'pnl_delta': -171880.05, 'pf_delta': 0.6711, 'dd_delta': 93799.96, 'effective': False}**
6. T15 (O2) effective: **{'pnl_delta': -191689.67, 'pf_delta': -0.1844, 'dd_delta': 30200.0, 'effective': False}**
7. VWAP (O3) effective: **{'pnl_delta': -53279.57, 'pf_delta': 1.3211, 'dd_delta': 83500.0, 'effective': False}**
8. ADX (O4) effective: **{'pnl_delta': -126978.27, 'pf_delta': 0.2656, 'dd_delta': 61101.31, 'effective': False}**
9. EMA (O5) effective: **{'pnl_delta': -55479.69, 'pf_delta': 1.2161, 'dd_delta': 94209.72, 'effective': False}**
10. PBv2 improvement room: **True**
11. Adoption candidates: **[]** (adopt_not_allowed=True)
