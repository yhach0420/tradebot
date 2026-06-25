# Phase404 — No Progress Exit Shadow

Generated: 2026-06-25T07:15:09+09:00
Period: 20260529 – 20260615
Verdict: **no_adopt_candidate**

Phase404: hold=900.0s mfe<0.8% pnl<0.2% hi=none vwap=none delta=¥274912.4 rescued=27/27 3905=¥-30498.84 no_adopt

## Mandatory analysis

1. long_hold_loser rescued: 27/27
2. MFE<0.5% improved: 20/20
3–4. Focus symbols: see tables below
5. saved_loss_yen: ¥485657.7
6. lost_upside_yen: ¥394149.14
7. net_delta_yen: ¥274912.4
8. affected_trade_count: 668
9. long_hold_loser_count: 36

## Baseline

| total_pnl_yen_100 | ¥127467.6 |
| profit_factor | 1.1236 |
| long_hold_loser_count | 27 |

## Phase402 reference

| net_delta | ¥204112.4 |
| 3905 damage | ¥-21999.71 |
| 4062 damage | ¥-7500.78 |

## Best policy

- hold_sec=900.0 max_mfe<0.8%
- current_pnl<0.2%
- high_update=none vwap=none
- net_delta: ¥274912.4 | adopt: False
- long_hold_loser: 36 (Δ9)
- rescued in baseline cohort: 27/27
- 3905: ¥-30498.84 | 4062: ¥5498.99

## Best 3905 preservation (vs Phase402)

- hold=1800.0s mfe<0.8% pnl<0.2%
- net_delta: ¥156872.4 | 3905 damage: ¥-21498.84
- long_hold_loser_count: 32

### Good long holds

| symbol | baseline | shadow | delta |
|--------|----------|--------|-------|
| 3905.T | ¥23498.84 | ¥-7000.0 | ¥-30498.84 |
| 4047.T | ¥15499.4 | ¥32000.0 | ¥16500.6 |
| 4062.T | ¥43501.01 | ¥49000.0 | ¥5498.99 |
| 9984.T | ¥13400.68 | ¥5300.0 | ¥-8100.68 |

### Bad long holds

| symbol | baseline | shadow | delta |
|--------|----------|--------|-------|
| 3915.T | ¥-4000.04 | ¥1800.0 | ¥5800.04 |
| 4078.T | ¥-7998.97 | ¥-500.0 | ¥7498.97 |
| 6055.T | ¥-3799.49 | ¥-1200.0 | ¥2599.49 |
| 7220.T | ¥-63499.5 | ¥-54000.0 | ¥9499.5 |

## Constraints

- shadow / research のみ
