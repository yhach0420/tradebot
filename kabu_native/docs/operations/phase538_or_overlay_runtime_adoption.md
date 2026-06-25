# Phase538 — OR Overlay Runtime Adoption

Production adoption of OR Open Strength Overlay on mainline ENTRY with split CAP (PBv2=4, OR=1).

## Adopted configuration

| Item | Value |
|------|-------|
| Universe | Core10 + Dynamic40 |
| CAP | PBv2=4, OR=1, total=5 (split pools) |
| OR definition | O_R003_OR + OS9 open strength proxy |
| Rollback | `or_overlay_enabled: false` in pilot YAML |

## Runtime changes

- `or_overlay_entry.py` — O_R003 day-high + updates≤8, OS9/day_leader reason, OR gate
- `or_overlay_cap.py` — split pool CAP (pools do not block each other)
- `pilot_runner.py` — PBv2 gate first; on reject, OR overlay fallback
- Daily summary — `or_entry_count`, `or_exit_count`, `or_active_positions`, `or_realized_pnl`, `or_unrealized_pnl`, `or_win_rate`, `or_pf`, `or_blocked_count`, `or_cap_full_count`, `pbv2_count`, `or_count`
- Discord ENTRY — `ENTRY_TYPE` (PBV2 / OR_OVERLAY), `OR_REASON` (open_strength / day_leader)

## Acceptance review (5 trading days)

1. OR entry count
2. OR PnL
3. OR win rate
4. OR PF
5. PBv2 PnL degradation (none expected)
6. CAP collision count (`or_cap_full_count`, `or_blocked_count`)
7. OR pool utilization (`or_pool_utilization`)

## Verdict

```text
phase538_or_overlay_runtime_adopted
```

Run:

```bash
python scripts/run_phase538_or_overlay_runtime_adoption.py
```

## Notes

- No long-term Shadow migration; direct adoption with config-only rollback.
- Adoption basis: Phase534–537 (open_strength hypothesis, CAP_SPLIT_4_1, universe OK, PBv2 intact, net_substitution positive, 9/9 criteria).
