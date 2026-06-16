# Phase402 — Time-Decayed MFE / Stop Shadow

Generated: 2026-06-16T00:01:17+09:00
Period: 20260529 – 20260615
Verdict: **adopt_candidate_found**

Phase402: time_decay_mfe threshold=900.0s MFE=0.3% stop=None% ΔPnL ¥204112.4 long_hold_loser Δ-1 bad_rescue ¥16299.72

## Baseline (Phase399 position_cap_accepted)

| Metric | Value |
|--------|-------|
| total_pnl_yen_100 | ¥127467.6 |
| profit_factor | 1.1236 |
| trade_count | 755 |
| win_rate | 0.5166 |
| max_drawdown_yen_100 | ¥105301.93 |
| long_hold_loser_count | 27 |
| stop_hit_count | 175 |
| trailing_mfe_count | 283 |
| session_close_count | 45 |

## Long-hold loser cohort (27)

- baseline total: ¥-118740.04
- best-policy shadow total: ¥-59940.0
- cohort delta: ¥58800.04
- improved / worsened / unchanged: 19 / 6 / 2

## Policy type comparison

| policy | variants | adopt | best net_delta | best long_hold_loser_Δ |
|--------|----------|-------|----------------|------------------------|
| combined_20m | 16 | 0 | ¥219172.4 | 10 |
| combined_30m | 16 | 0 | ¥189292.4 | 5 |
| time_decay_mfe | 12 | 2 | ¥208932.4 | -2 |
| time_decay_stop | 12 | 0 | ¥191892.4 | 9 |

## Focus symbols

### Good long holds (should not damage)

| symbol | trades | baseline | shadow (best) | delta |
|--------|--------|----------|---------------|-------|
| 3905.T | 3 | ¥29499.71 | ¥7500.0 | ¥-21999.71 |
| 4047.T | 1 | ¥10999.95 | ¥12500.0 | ¥1500.05 |
| 4062.T | 2 | ¥38500.78 | ¥31000.0 | ¥-7500.78 |
| 9984.T | 1 | ¥16700.11 | ¥16700.0 | ¥-0.11 |

### Bad long holds (should rescue)

| symbol | trades | baseline | shadow (best) | delta |
|--------|--------|----------|---------------|-------|
| 3915.T | 2 | ¥-4999.97 | ¥-2500.0 | ¥2499.97 |
| 4078.T | 2 | ¥-10499.84 | ¥500.0 | ¥10999.84 |
| 6055.T | 1 | ¥-2599.91 | ¥200.0 | ¥2799.91 |
| 7220.T | 1 | ¥-5999.9 | ¥-6000.0 | ¥-0.1 |

## Top adopt candidates

Best: `time_decay_mfe` threshold=900.0s mfe_after=0.3% stop_after=None%

- net_delta_yen: ¥204112.4
- saved_loss_yen: ¥449807.95
- lost_upside_yen: ¥375640.66
- long_hold_loser_delta: -1
- good_long_hold_damage_yen: ¥-29500.62
- bad_long_hold_rescue_yen: ¥16299.72

## Grid top 10 by net_delta_yen

| policy | threshold | mfe_after | stop_after | net_delta | long_hold_loser_Δ | adopt |
|--------|-----------|-----------|------------|-----------|---------------------|-------|
| combined_20m | 1200.0 | 0.3 | -0.4 | ¥219172.4 | 10 | False |
| combined_20m | 1200.0 | 0.3 | -0.6 | ¥217582.4 | 7 | False |
| combined_20m | 1200.0 | 0.4 | -0.6 | ¥217422.4 | 7 | False |
| combined_20m | 1200.0 | 0.2 | -0.4 | ¥216342.4 | 10 | False |
| combined_20m | 1200.0 | 0.2 | -0.6 | ¥209432.4 | 10 | False |
| combined_20m | 1200.0 | 0.3 | -0.8 | ¥209412.4 | 6 | False |
| combined_20m | 1200.0 | 0.4 | -0.4 | ¥209412.4 | 10 | False |
| time_decay_mfe | 900.0 | 0.4 | None | ¥208932.4 | -2 | False |
| combined_20m | 1200.0 | 0.4 | -0.8 | ¥206452.4 | 5 | False |
| time_decay_mfe | 900.0 | 0.2 | None | ¥204782.4 | 4 | False |

## Constraints

- Runtime反映なし
- YAML変更なし
- Exit本番変更なし
- shadow / research のみ
