# Phase441 — Boundary vs No Progress Overlap Audit

Generated: 2026-06-18T23:34:35+09:00
Verdict: **boundary_redundant**
Period: 20260529..20260618

## Exit-only comparison (A–D)

| scenario | accepted | PnL | PF | maxDD | delta vs baseline |
|----------|----------|-----|-----|-------|-------------------|
| A_baseline | 810 | 47567.98 | 1.0367 | 158700.0 | 0.0 |
| B_no_progress | 810 | 135088.79 | 1.1145 | 154800.0 | 87520.81 |
| C_boundary | 810 | 242768.07 | 1.305 | 75690.62 | 195200.09 |
| D_no_progress_plus_boundary | 810 | 148568.07 | 1.1424 | 166300.87 | 101000.09 |

## Overlap

- No Progress fires: **90**
- Boundary fires: **326**
- Both fire: **90**
- Boundary only: **236** (ΔPnL -24882.95 yen)
- No Progress only: **0** (ΔPnL 0.0 yen)
- Overlap improvement PnL: **106583.41** yen

## Capacity-aware

| variant | accepted | PnL | delta vs baseline |
|---------|----------|-----|-------------------|
| NP_capacity_aware | 817 | 144588.41 | 97020.43 |
| Boundary_capacity_aware | 818 | 134567.69 | 86999.71 |
| Combined_capacity_aware | 818 | 134567.69 | 86999.71 |

## Reference (prior phases)

- Phase427 NP exit-only ref: 81921 yen
- Phase429A NP capacity ref: 97520 yen
- Phase440 Boundary exit-only ref: 195200 yen

## Adoption rank

1. Boundary_exit_only
2. Combined_exit_only
3. No_Progress_capacity_aware
4. No_Progress_exit_only
5. Boundary_capacity_aware
6. Combined_capacity_aware

## 必須回答

- 1_boundary_fire_count: 326
- 2_no_progress_fire_count: 90
- 3_overlap_count: 90
- 4_boundary_only_count: 236
- 5_no_progress_only_count: 0
- 6_boundary_only_improvement_pnl_yen: -24882.95
- 7_no_progress_only_improvement_pnl_yen: 0.0
- 8_combined_exit_only_pnl_yen: 148568.07
- 9_boundary_has_independent_value: False
- 10_adoption_candidate_rank: ['Boundary_exit_only', 'Combined_exit_only', 'No_Progress_capacity_aware', 'No_Progress_exit_only', 'Boundary_capacity_aware', 'Combined_capacity_aware']

## 判定

**boundary_redundant**