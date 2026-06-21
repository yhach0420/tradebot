# Phase452A — Weak Shape Fidelity Audit

Generated: 2026-06-19T23:21:31+09:00
Period: 20260529..20260619
Eval pool: Momentum:low + (Board:mid OR Board:high) + NOT high_drift

## Mandatory answers

1. EOD reject count: **264**
2. Runtime reject count: **145**
3. Agreement count: **118**
4. Precision: **0.8138**
5. Recall: **0.447**
6. EOD-only rejects: **146**
7. Runtime-only rejects: **27**
8. PnL delta (Runtime − EOD): **12881.09** yen
9. 6976 delta (Runtime − EOD): **34500.43** yen
10. Verdict: **`runtime_weaker`**

## Confusion (eval pool n=681)

| Metric | Value |
|--------|-------|
| EOD reject | 264 |
| Runtime reject | 145 |
| Agreement | 118 |
| Precision | 0.8138 |
| Recall | 0.447 |
| EOD-only | 146 (PnL 0.0 yen) |
| Runtime-only | 27 (PnL 0.0 yen) |

## CAP5 replay

| Variant | PnL | PF | Accepted | 6976 |
|---------|-----|-----|----------|------|
| Baseline (no weak shape) | 82470.48 | 1.0773 | 636 | 130999.3 |
| EOD E_weak_shape | 134531.13 | 1.2105 | 378 | 116000.03 |
| Runtime weak_shape | 147412.22 | 1.1925 | 496 | 150500.46 |

## EOD vs Runtime definition delta

- **EOD (451B E):** `eod_shape_class` in (`opening_peak`, `slow_opening_peak`); `uptrend` passes.
- **Runtime (452):** Intraday timing + pullback at ENTRY; uptrend pass via recent high update or r10/r15/r30.

Outputs:
- `phase452a_weak_shape_fidelity_summary.json`
- `phase452a_weak_shape_fidelity_detail.csv`
- `phase452a_weak_shape_eod_only.csv`
- `phase452a_weak_shape_runtime_only.csv`
