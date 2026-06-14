# Runtime Dependency Graph

Generated: 2026-06-14 21:53 JST | Production: **Stack C**

```
Stack C (production)
├── Universe
│   ├── Phase113
│   ├── Phase117
│   ├── Phase148
│   └── Phase269
├── Entry
│   ├── Phase153b
│   ├── Phase267
│   ├── Phase314
│   ├── Phase355
│   ├── Phase364
│   └── NP-entry-scan
├── Exit
│   ├── Phase332
│   └── structural v1
├── Position
│   └── q070_cap3
├── Risk
│   ├── YAML daily_loss
│   └── risk_cluster
├── Discord
│   ├── Phase281
│   └── Phase333
├── Monitoring
│   ├── Phase55
│   ├── Phase148
│   ├── Phase317
│   ├── Phase376
│   ├── Phase377
│   └── Phase373
├── Shadow
│   ├── Phase255
│   ├── Phase256
│   ├── Phase262
│   ├── Phase266
│   ├── Phase273
│   ├── Phase274
│   └── Phase387
└── Research
    ├── Phase272
    ├── Phase273
    ├── Phase274
    ├── Phase388
    └── Phase389

CAP=2: Phase387/388/389 → Research branch (not production runtime)
```

## Current Runtime Provenance

| Component | Phase | Adoption Date | Evidence |
| --- | --- | --- | --- |
| Universe top50 | 113, 117 | 2026-05-27 | Production runtime Stack C |
| Core10+Dynamic40 price-risk | 269, 148 | 2026-05-29 | AM/PM refresh + price-risk filter |
| Entry score v2 | 314, 267 | 2026-06-07 | Rule reduction 2-token; quality reject off |
| Price risk guard | 153b | 2026-05-27 | YAML entry_price_risk_guard |
| Pullback guard | 355 | 2026-06-13 | +100,400 yen vs baseline (Phase365) |
| Near day-high guard | 364 | 2026-06-13 | +140,200 yen 6/12 replay (Phase363) |
| Board dynamic exit | 332 | 2026-06-13 | production_adoption_ok=true |
| CAP3 | q070_cap3 | 2026-05-18 | runtime max_concurrent_positions=3 |
| CAP2 | 388, 389, 387 | 2026-06-14 | **Research only** — runtime cap3 maintained |
| Canonical summary | 333 | 2026-06-13 | kabutrade0612 canonical 100-share yen |
| Cap-blocked Discord | 281 | 2026-06-13 | Discord channel split |
