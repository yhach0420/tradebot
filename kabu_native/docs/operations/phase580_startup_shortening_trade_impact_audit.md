# Phase580 — Startup Shortening Trade Impact Audit

**Verdict:** `phase580_startup_shortening_trade_impact_audit_done`
**Production continue OK:** True

## Key finding

Historical sessions cannot replay gap-period ENTRY impact: `entry_scan_audit.jsonl` only contains evals from `first_eval` onward, and `push_jsonl` ticks in the gap window are absent (first_push coincides with session_ready).
Post-cache, eval may start near policy_start; live monitoring required to quantify trade impact.

## Mandatory answers

1. Population changes: True
2. Additional entry candidates (gap evals): 0
3. Additional accepted trades (replayable): 0
4. Added PnL: 0
5. Added PF: 0
6. Added trades net: neutral
7. Downstream side effects: True
8. CAP conflicts: 0
9. stop_low_mfe increase: 0
10. AM/PM larger impact: am
11. Phase558 baseline still valid: True
12. New comparison baseline needed: True
13. Runtime change needed: False
14. Next phase: phase581_startup_shortening_live_monitor

- Replayable sessions: 0
- Non-replayable sessions: 41
- Accept decisions in gap: 0