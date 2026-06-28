# Phase578 — Phase558 vs Phase577 Population Audit

**Verdict:** `phase578_phase558_vs_phase577_population_audit_done`

## Conclusion

PF gap **2.1397 → 1.0273** is primarily due to **population_and_methodology_difference (cap_extension_sim + guard_replay + period scope; not runtime degradation)**, not Runtime performance degradation.

## Mandatory answers

1. Phase558 population: combined 432 trades (20260616-20260625 live accepted 130 + cap extension 302); guard replay + cap sim
2. Phase577 population: raw canonical observer_exit 2990 trades (20260529-20260626); no guard replay, no cap extension
3. 432→2990 reason: Phase577 counts all raw observer_exit across 21 days/all sessions (1681 pre-live + 0 live excess + 1179 guard-blocked + 0 post-period); Phase558 uses 130 guard-filtered live + 302 cap-sim not in events
4. PF on Phase558 population only: **2.1397**
5. PF on 2990 population: **1.0273**
6. Runtime performance changed: False
7. Comparable: True
8. PF drop root cause: **population_and_methodology_difference (cap_extension_sim + guard_replay + period scope; not runtime degradation)**
9. Future comparison baseline: Phase558 D_phase558 combined (guard replay + cap extension, 20260529-20260625)
10. Phase577 conclusion valid: False
11. Runtime change needed: False
12. Next phase: phase579_guard_aware_profit_source_monitor

## Category counts (2990 breakdown)

{
  "historical_pre_live": 1681,
  "phase558_live_blocked": 1179,
  "phase558_live_accepted": 130,
  "cap_extension_simulated_only": 302
}