# Phase395 — Position-CAP Shadow Report

Generated: 2026-06-15T21:47:46+09:00

## Shadow Specification

| Rule | Behavior |
|------|----------|
| Entry | Accept when `open_positions < 3` on shadow ledger |
| Hold | Slot occupied until structural EXIT |
| Exit | Release slot on structural exit event |
| Session close | Force-close remaining at 15:23 |
| PnL | 100-share yen from `structural_trades` |
| Capital path | 1.5M / lev2 / 100 / CAP3 / fixed_stop_1p2 |

## Results

| Metric | Runtime (virtual-hold) | Shadow (position-cap) |
|--------|------------------------|------------------------|
| Accepted | 90 | 22 |
| CAP rejects | 1703 | 58 |
| Max active | 3 (gate slots) | 3 |
| Observer max open | 16 | — |
| Session-close EXIT burst | 0 (shadow) | |
| PnL (100 shares) | — | ¥18700.0 |
| Capital path PnL | — | ¥18700.0 |
| Capital path final equity | — | ¥1518700.0 |

## Artifacts

- `results/reports/phase395_position_cap_shadow_events.csv`
- `results/reports/phase395_position_cap_shadow_summary.json`

**No production Runtime / Discord changes in Phase395.**
