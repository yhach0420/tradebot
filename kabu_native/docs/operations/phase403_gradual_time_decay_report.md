# Phase403 — Gradual Time-Decay MFE Shadow

Generated: 2026-07-12T02:10:25+09:00
Period: 20260529 – 20260615
Verdict: **no_adopt_candidate**

Phase403: linear_decay start=900.0s initial=0.6% floor=0.2% delta=¥222472.4 3905=¥-28498.84 4062=¥7498.99 vs402=True no_adopt

## Mandatory answers

1. Best policy: `{'policy_id': 'linear_decay', 'decay_start_sec': 900.0, 'initial_mfe_pct': 0.6, 'floor_mfe_pct': 0.2, 'linear_decay_per_min': 0.05, 'exp_decay_lambda': None}`
2. PnL improvement: ¥222472.4
3. PF improvement: 0.1472
4. long_hold_loser reduction: -3
5. 3905.T damage: ¥-28498.84
6. 4062.T damage: ¥7498.99
7. 4078.T rescue: ¥-5001.03
8. Better than Phase402: True (PnL: True, 3905: False, 4062: True)
9. Adopt candidate: False

## Baseline (Phase399 position_cap)

| total_pnl_yen_100 | ¥127467.6 |
| profit_factor | 1.1236 |
| long_hold_loser_count | 27 |

## Phase402 reference (step decay best)

| net_delta_yen | ¥204112.4 |
| 3905.T damage | ¥-21999.71 |
| 4062.T damage | ¥-7500.78 |
| 4078.T rescue | ¥10999.84 |

## Best gradual policy

- policy: `linear_decay`
- decay_start_sec: 900.0
- initial_mfe_pct: 0.6
- floor_mfe_pct: 0.2
- linear_decay_per_min: 0.05
- exp_decay_lambda: None
- net_delta_yen: ¥222472.4
- adopt_candidate: False

## Best policy with long_hold_loser improvement

- `linear_decay` start=900.0s initial=0.6% floor=0.4%
- net_delta: ¥203532.4 | long_hold_loser Δ-1
- 3905 damage: ¥-28498.84 | 4062: ¥7498.99

## Best 3905.T preservation

- `exp_decay` start=600.0s lambda=0.01
- 3905 damage: ¥-998.84 | net_delta: ¥-28657.6

## Focus symbol comparison (best vs Phase402)

| symbol | Phase402 delta | Phase403 best delta |
|--------|----------------|---------------------|
| 3905.T | ¥-21999.71 | ¥-28498.84 |
| 4062.T | ¥-7500.78 | ¥7498.99 |
| 4047.T | ¥n/a | ¥n/a |
| 9984.T | ¥n/a | ¥n/a |
| 4078.T | ¥10999.84 | ¥-5001.03 |
| 6055.T | ¥n/a | ¥n/a |
| 3915.T | ¥n/a | ¥n/a |
| 7220.T | ¥n/a | ¥n/a |

## Constraints

- Runtime / YAML / Entry / Exit / Discord 変更なし
- shadow / research のみ
