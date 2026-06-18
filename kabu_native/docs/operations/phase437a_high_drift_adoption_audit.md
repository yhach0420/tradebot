# Phase437A — High Drift Adoption Audit

Generated: 2026-06-18T21:45:36+09:00
Period: 20260529..20260618
**Verdict:** `6976_concentration_risk_shadow_continue`

## Mandatory answers

1. VWAP only removed: **0**
2. High Drift only removed: **39**
3. Both removed: **0**
4. High Drift standalone PnL: **138,567 yen**
5. High Drift standalone PF: **1.124**
6. top_day_share: **0.9253**
7. top_symbol_share: **0.7473**
8. VWAP fully replaceable: **True**
9. Runtime adoption candidate: **False**
10. Shadow vs adoption: **shadow_continue_concentration_risk**

## Variant comparison

| variant | trades | PnL | PF | stop_rate | maxDD | removed |
|---------|--------|-----|-----|-----------|-------|---------|
| baseline | 810 | 47,568 | 1.0367 | 0.2716 | 158,700 | 0 |
| legacy_vwap | 810 | 47,568 | 1.0367 | 0.2716 | 158,700 | 0 |
| high_drift | 771 | 138,567 | 1.124 | 0.2672 | 102,282 | 39 |
| legacy_vwap_plus_high_drift | 771 | 138,567 | 1.124 | 0.2672 | 102,282 | 39 |

## Concentration (High Drift delta vs baseline)

- top_day_share: 0.9253 (threshold ≤0.5)
- top_symbol_share: 0.7473 (threshold ≤0.3)
- delta excluding 6976 removals: 22,999 yen
- delta excluding 20260618 removals: 6,799 yen
- delta excluding 6976 on 20260618: 39,499 yen

## 6976 analysis

- removed: 7 trades, PnL -68,000 yen
- removed stops: 3
- improvement contribution rate: 0.7473
- 20260618 removed: 4 (-51,500 yen)

## VWAP overlap

| bucket | count | PnL | PF | 6976 |
|--------|-------|-----|-----|------|
| vwap_only | 0 | 0 | None | 0 |
| high_drift_only | 39 | -90,999 | 0.4876 | 7 |
| both | 0 | 0 | None | 0 |
| neither | 771 | 138,567 | 1.124 | 37 |

## Interpretation

- Baseline: 47,568 yen, PF 1.0367
- High Drift: 138,567 yen, PF 1.124
- Legacy VWAP removes 0 trades this period (vwap_dev>0 on all pullback candidates).
- High Drift is orthogonal to VWAP; combined variant equals High Drift alone.
- High concentration in 20260618 / 6976.T drives delta — not a pure 6976-only guard but period-specific validation required before Runtime.

Runtime/YAML/Entry/Exit/Order/Discord changes **forbidden** (audit only).
