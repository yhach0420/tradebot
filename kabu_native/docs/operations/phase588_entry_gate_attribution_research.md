# Phase588 — ENTRY Gate Attribution Research

**Verdict:** `phase588_entry_gate_attribution_research_done`
**Period:** 20260529–20260626 (41 sessions)

## Scope

Counterfactual ENTRY gate audit (Board focus) + CAP replay ablation.
No Runtime / ENTRY / Guard / Universe changes.

## Mandatory answers

1. Largest reject gate: **push_stale**
2. Board rejects: **138365**
3. Board reject counterfactual PnL/PF: **-14968046.18** / **0.5249** (unavailable=130124)
4. Board OFF improves (quality-safe): **False** (raw ΔPnL=212458.8; quality pass=False)
5. Board relaxed improves: **True** (ΔPnL=40400.22)
6. Board strengthen improves: **False**
7. Board necessary: **True** (board_required)
8. Momentum necessary: **True**
9. Volume necessary: **False**
10. ClusterGuard necessary: **False**
11. StopLowMFE necessary: **True**
12. LateChase necessary: **True**
13. CAP primary cause: **False**
14. Best gate config: **no_volume**
15. Runtime change candidate: **True**
16. Next phase: **phase589_board_gate_pilot_shadow**

Counterfactual match rate: 3.59%
Baseline replay PnL/PF: 499952.93 / 4.6573

## Outputs

- `results/reports/phase588_gate_reject_summary.csv`
- `results/reports/phase588_gate_reject_counterfactual.csv`
- `results/reports/phase588_board_gate_detail.csv`
- `results/reports/phase588_board_ablation_replay.csv`
- `results/reports/phase588_gate_ablation_replay.csv`
- `results/reports/phase588_gate_combination_replay.csv`
- `results/reports/phase588_board_entry_quality.csv`
- `results/reports/phase588_board_daily_symbol_impact.csv`
- `results/reports/phase588_report.json`