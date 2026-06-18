# Phase443 — Full Runtime Combined Capital Simulation

Generated: 2026-06-19T00:01:52+09:00
Verdict: **adopt_combined_runtime**
Period: 20260529..20260618

## Comparison (CAP5 capacity-aware)

| Scenario | Final equity | Δ vs A | Accepted | PF | MaxDD | Stop rate | HD reject | NP exit |
|----------|-------------|--------|----------|-----|-------|-----------|-----------|---------|
| A_baseline_phase423_424 | 1547567.98 | 0.0 | 810 | 1.0367 | 158700.0 | 0.2654 | 0 | 0 |
| B_high_drift_only | 1623467.4 | 75899.42 | 779 | 1.1069 | 102282.41 | 0.2298 | 45 | 0 |
| C_no_progress_only | 1644588.41 | 97020.43 | 817 | 1.1208 | 143300.0 | 0.1848 | 0 | 90 |
| D_high_drift_no_progress | 1694788.19 | 147220.21 | 781 | 1.1826 | 83500.89 | 0.1921 | 45 | 85 |

## Mandatory answers

1. **最終資産 (D)**: 1694788.19 円
2. **現行との差**: 147220.21 円
3. **6/18損失削減**: 60500.0 円 (baseline -98200.0 → D -37700.0)
4. **High Drift単独寄与**: 75899.42 円
5. **No Progress単独寄与**: 97020.43 円
6. **併用相互作用**: -25699.64 円
7. **明日Runtime妥当性**: adopt_combined_runtime (valid=True)

## Attribution detail

- High Drift only (B−A): 75899.42 円
- No Progress only (C−A): 97020.43 円
- Combined (D−A): 147220.21 円
- Interaction: -25699.64 円
