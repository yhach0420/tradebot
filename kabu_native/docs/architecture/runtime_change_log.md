# Runtime Change Log

Generated: 2026-06-14 21:53 JST | Source: `kabu_native/docs/audits/full_phase_history_audit.csv`
Current generation: **9** (Current)

| Date | Runtime Version | Universe | Entry | Exit | CAP | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-05-06 | Genesis | manual CSV / screening | prototype gate | not integrated | — | first commit; Discord / screening foundation |
| 2026-05-18 | Observer v1 | pre top50 | quality + structural | stop/session/overlap | 3 | Phase55 small paper observer |
| 2026-05-29 | Core10+Dynamic40 v1 | 113/117 top50 | quality≥0.70 + price risk | structural + fade trials | 3 | two-layer universe |
| 2026-06-04 | Trailing Shadow | core10+d40 + AM/PM(148) | quality + score shadow | Phase174 fixed 0.8%/50% shadow | 3 | trailing-MFE shadow policy |
| 2026-06-07 | ScoreV2 Transition | core10+d40 price-risk | Phase314 score_v2≥3 | fixed trailing + structural | 3 | depart v1 multi-factor score |
| 2026-06-09 | Pre-332 | core10+d40 price-risk | score_v2 + 153b | Phase332 replay OK (YAML pending) | 3 | board-dynamic EXIT adoption OK |
| 2026-06-12 | 6/12 Incident Runtime | core10+d40 price-risk | score_v2 + 153b; guards off | 174 legacy or 332 transition | 3 | 6/12 AM Dynamic40 losses |
| 2026-06-13 | Stack C | 113/117/269 + refresh | 267/314 + 355 + 364 + freshness | Phase332 board-dynamic | 3 | kabutrade0612 recovery commit |
| 2026-06-14 | Current | Stack C unchanged | Stack C unchanged | Stack C unchanged | 3 | forward shadows; CAP=2 research only |

## Runtime Delta Timeline

| Date | Added | Removed | Replaced |
| --- | --- | --- | --- |
| 2026-05-18 | Phase55 observer | — | — |
| 2026-05-29 | Phase113/117 top50 | — | — |
| 2026-06-04 | Phase174 trailing shadow | — | — |
| 2026-06-07 | Phase314 score_v2 | quality≥0.70 reject (267 path) | — |
| 2026-06-09 | Phase332 EXIT (replay OK) | — | — |
| 2026-06-12 | — | — | — (incident; guards not yet applied) |
| 2026-06-13 | Phase355, Phase364, Phase333, Phase281, NP-scan | Phase174 production trailing | Phase174 → Phase332 |
| 2026-06-14 | Phase273/274 forward shadow hooks | — | Phase270/271 → Phase272 (research) |
