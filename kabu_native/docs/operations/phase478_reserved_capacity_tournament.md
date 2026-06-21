# Phase478 — Strategy Reserved Capacity Tournament

**Verdict:** `capacity_conflict_only`
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良CAP配分 | **A (PBv2 CAP5 / PB CAP0 (baseline))** |
| 2 | A比ΔPnL | **0.0** |
| 3 | PF | **1.9886** |
| 4 | maxDD | **71000.0** |
| 5 | PB寄与 | **0.0** |
| 6 | PBv2寄与 | **402962.82** |
| 7 | 6976依存度 | **0.5484** |
| 8 | 独立PB価値 | **44900.0** |
| 9 | CAP競合解消効果 | **327500.0** |
| 10 | PB独立戦略 | **True** |
| 11 | Runtime候補 | **False** |
| 12 | Shadow候補 | **reserved_pb_pool** |
| 13 | 次アクション | Verdict: capacity_conflict_only; CAP competition confirmed; PB marginal alone (+E ref) but reserved split on CAP5 loses vs A; Independent book ref E: +44900.0 vs A (not same capital); Best combined CAP: A PnL 402962.82 vs baseline A 402962.82 |

## Tournament results

| Var | PBv2 cap | PB cap | Total PnL | PBv2 | PB | PF | maxDD | acc | Δ vs A |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 5 | 0 | 402962.82 | 402962.82 | 0.0 | 1.9886 | 71000.0 | 256 | 0.0 |
| B | 4 | 1 | 197122.82 | 330162.82 | -133040.0 | 1.3257 | 94400.0 | 293 | -205840.0 |
| C | 3 | 2 | 211222.82 | 299262.82 | -88040.0 | 1.2948 | 122600.0 | 321 | -191740.0 |
| D | 2 | 3 | 105061.84 | 228861.84 | -123800.0 | 1.147 | 139900.0 | 295 | -297900.98 |
| E | 5 | 5 | 447862.82 | 402962.82 | 44900.0 | 1.3717 | 215800.0 | 423 | 44900.0 |

- Shared dual (Phase477-style CAP5 shared): **{'total_pnl_yen': 75462.82, 'accepted_count': 311}**
- Reference E independent: **{'variant': 'E', 'label': 'Independent PBv2 CAP5 + PB CAP5', 'pbv2_cap': 5, 'pb_cap': 5, 'total_pnl_yen': 447862.82, 'pbv2_pnl_yen': 402962.82, 'pb_pnl_yen': 44900.0, 'profit_factor': 1.3717, 'max_drawdown_yen': 215800.0, 'accepted_count': 423, 'pbv2_accepted': 256, 'pb_accepted': 167, 'pbv2_cap_utilization': 0.0, 'pb_cap_utilization': 0.0, 'delta_pnl_vs_A': 44900.0, 'delta_pf_vs_A': -0.6169, 'delta_maxdd_vs_A': 144800.0}**

## Capacity rescue summary

- {'B': {'blocked_in_pbv2_pool': 74, 'rescued_by_pb_pool': 10, 'rescued_winners': 3, 'rescued_losers': 7, 'rescued_pnl_yen': 43200.0}, 'C': {'blocked_in_pbv2_pool': 74, 'rescued_by_pb_pool': 23, 'rescued_winners': 5, 'rescued_losers': 18, 'rescued_pnl_yen': 68500.0}, 'D': {'blocked_in_pbv2_pool': 74, 'rescued_by_pb_pool': 27, 'rescued_winners': 7, 'rescued_losers': 20, 'rescued_pnl_yen': 141600.0}}

**判定:** `capacity_conflict_only`