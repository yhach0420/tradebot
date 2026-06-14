# Runtime Adoption Funnel

Generated: 2026-06-14 21:53 JST | Phases: **331**

## Overall Status

| Status | Count |
| --- | --- |
| Adopted | 21 |
| Rejected | 8 |
| Removed | 5 |
| Research | 289 |
| Shadow | 4 |
| Superseded | 4 |
| **Total** | 331 |

## By Category

| Category | Total | Adopted | Rejected | Shadow | Research | Superseded | Removed | Observe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Universe | 21 | 3 | 1 | 0 | 15 | 1 | 1 | 0 |
| Entry | 41 | 5 | 4 | 0 | 31 | 1 | 0 | 0 |
| Exit | 24 | 1 | 2 | 0 | 21 | 0 | 0 | 0 |
| Position | 22 | 1 | 0 | 0 | 20 | 1 | 0 | 0 |
| Risk | 5 | 0 | 0 | 1 | 4 | 0 | 0 | 0 |
| Sizing | 10 | 0 | 0 | 1 | 9 | 0 | 0 | 0 |
| Capital | 2 | 0 | 0 | 0 | 1 | 0 | 1 | 0 |
| Discord | 8 | 3 | 0 | 0 | 5 | 0 | 0 | 0 |
| Monitoring | 165 | 7 | 1 | 0 | 153 | 1 | 3 | 0 |
| Data | 19 | 1 | 0 | 1 | 17 | 0 | 0 | 0 |
| Replay | 10 | 0 | 0 | 0 | 10 | 0 | 0 | 0 |
| Documentation | 4 | 0 | 0 | 1 | 3 | 0 | 0 | 0 |
| **合計** | 331 | 21 | 8 | 4 | 289 | 4 | 5 | 0 |

## Adopted Then Removed

| Phase | Adopted Date | Removed Date | Replacement | Reason |
| --- | --- | --- | --- | --- |
| Phase114 | 2026-05-27 | 2026-05-29 | Phase148 | 12:25 PM regen → 10:00/14:30 intraday refresh |
| Phase13 | 2026-05-17 | 2026-05-18 | Phase148 | no_entry_until 09:30 → session window management |
| Phase174 | 2026-06-04 | 2026-06-13 | Phase332 | fixed 0.8%/50% trailing → board-dynamic trailing |
| Phase255 | 2026-06-04 | 2026-06-04 | 253,254,256 | Superseded by sector heat forward shadow Phase255 |
| Phase263 | 2026-06-04 | 2026-06-04 | — | At 5M yen, dynamic_stop_risk_0p5 improves total shadow PnL vs fixed -1.2%. |
| Phase268 | 2026-06-04 | 2026-06-04 | — | research_complete |
| Phase270 | 2026-06-14 | 2026-06-14 | Phase272 | mixed leverage bucket → lev2.0 fixed |
| Phase271 | 2026-06-14 | 2026-06-14 | Phase272 | lev1.5 non-robust on 9-day sample |
| Phase273 | 2026-06-04 | 2026-06-14 | Phase274 | static bucket shadow superseded by auto-transition shadow |

**CAP=2 verdict:** Research (Phase387/388/389) — **not** production runtime.
