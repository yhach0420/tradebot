# Phase426 — Boundary Hold-Time Distribution Audit

Generated: 2026-06-17T20:54:17+09:00
Verdict: **boundary_low_value**

## Baseline (Phase423 canonical CAP5 accepted snapshot)

- accepted: 678
- boundary_eligible (hold>=300s): 373
- boundary_hit Phase423 reported: 0
- boundary_hit raw sim: 326
- non-trigger (raw sim): 47

Phase409 would_hit_count checks normalized shadow_exit_reason; boundary_mfe_exit/boundary_trail_exit map to other, so reported hit=0.

## Hold distribution (373 eligible)

| threshold | count |
|-----------|------:|
| >= 5m | 373 |
| >= 10m | 235 |
| >= 15m | 152 |
| >= 20m | 106 |
| >= 30m | 57 |
| >= 45m | 32 |
| >= 60m | 20 |

## 必須回答

1. hold counts: {'5m': 373, '10m': 235, '15m': 152, '20m': 106, '30m': 57, '45m': 32, '60m': 20}
2. primary non-trigger (47 true non-fires): **A** {'A': 47}
3. conditions too strict: False (raw hit rate 0.873995)
4. relaxation value: False
5. Phase405 reach count: 86
6. reach performance: {'win_rate': 0.314, 'avg_pnl_yen_100': -21.16, 'pf': 0.2101}
7. 6976.T rescue: False
8. 5016.T rescue: False
9. 3915.T rescue: False
10. continue research: False

Reported hit=0 is metric artifact; raw sim hit=326/373. True non-trigger=47, primary=A.

## 6/17 PM top-loss rescue

- **6976.T** hold=679.0s MFE=0.1741% MAE=-1.3429% boundary_hit=False rescue=False (eval_failed)
- **5016.T** hold=1202.0s MFE=0.3369% MAE=-1.28% boundary_hit=False rescue=False (eval_failed)
- **3915.T** hold=961.0s MFE=0.1886% MAE=-1.358% boundary_hit=False rescue=False (eval_failed)
- **5367.T** hold=287.0s MFE=0.0% MAE=-1.2048% boundary_hit=False rescue=False (eval_failed)
- **186A.T** hold=545.0s MFE=0.0726% MAE=-1.2346% boundary_hit=False rescue=False (eval_failed)
