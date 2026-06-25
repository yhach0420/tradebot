# Phase520 — G3_G4 Forward Shadow

**Verdict:** `phase520_g3_g4_forward_shadow_done`
**Forward period:** 20260616 – 20260622
**Frozen spread median (Phase519):** 63.78
**Completion met:** True (days=4, trades=28, reason=forward_data_ceiling)
**Forward data ceiling:** True (last_day=20260619)

## G3_G4 rules (overlay shadow only)

- day_high + updates<=8
- rolling_volume_percentile >= 80
- spread <= 63.78 (Phase519 median)
- Exit: PBv2 Exit replay

## Forward vs PBv2

| | G3_G4 Shadow | PBv2 (forward) |
|--|--------------|----------------|
| PnL | 40300.0 | -29750.0 |
| PF | 2.515 | 0.9005 |
| maxDD | 12500.0 | 118600.0 |
| Trades | 28 | 192 |

## Overlay quality

- true_breakout: 0.4643 (ref OR late=0.3372)
- late_breakout: 0.3214
- high_chase: 0.1071

## Dependency

- 6976 share: 0.0% (ref OR 71.92%)
- top10 trade share: 97.61% (ref OR 50.45%)

## Mandatory answers

1. Better than PBv2: **True**
2. PnL maintained: **True**
3. PF maintained: **True**
4. DD maintained: **True**
5. 6976 recurred: **False**
6. top10 recurred: **True**
7. late suppressed: **True**
8. high_chase suppressed: **True**
9. shadow continue value: **True**
10. adopt promotion: **False** (not allowed)
