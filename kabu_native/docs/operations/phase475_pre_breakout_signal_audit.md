# Phase475 — Pre-Breakout Signal Audit

**Verdict:** `pre_breakout_signal_found`
**Period:** 20260529–20260619
**Runners (symbol-days):** 191
**Primary analysis:** +2.0% breakout, 60s pre-window

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 急騰前に最も変化する特徴量 | **vwap_structure_score** |
| 2 | Cohen's d 上位 | 下表参照 |
| 3 | 情報利得上位 | 下表参照 |
| 4 | board寄与 | **0.1739** |
| 5 | VWAP寄与 | **3.5632** |
| 6 | 高値更新寄与 | **0.6591** |
| 7 | 出来高急増寄与 | **1.0828** |
| 8 | Momentum寄与 | **4.7661** |
| 9 | Trend後追い理由 | Current T-B (consecutive_above_ticks>=20 AND vwap_dev_pct>0) requires extended price>VWAP and many consecutive up-ticks — states only reachable after initial breakout acceleration, not at pre-breakout compression/coiling phase. |
| 10 | 最有力Pre-Breakout候補 | **vwap_dev_pct gt 0.6734** |
| 11 | 2条件候補 | **vwap_structure_score gt 2.5473 AND vwap_dev_pct gt 0.6734** |
| 12 | 3条件候補 | **vwap_structure_score gt 2.5473 AND vwap_dev_pct gt 0.6734 AND momentum_continuation_score gt 0.3094** |
| 13 | 過学習リスク | **False** |
| 14 | Replay候補 | **True** |
| 15 | 次アクション | Verdict: pre_breakout_signal_found; Design Phase476 frozen pre-breakout gate audit (no runtime yet); Lead candidate: PB1-vwap_dev_pct — vwap_dev_pct gt 0.6734; Replay candidate: True; Overfit risk: False |

## Cohen's d Ranking (primary window)

| rank | feature | group | d | winner_med | loser_med | MI |
|---:|---|---|---:|---:|---:|---:|
| 1 | vwap_structure_score | vwap | 1.1962 | 2.5473 | 0.7087 | 0.0989 |
| 2 | vwap_dev_pct | vwap | 1.0178 | 0.6734 | -0.0791 | 0.1126 |
| 3 | momentum_continuation_score | momentum | 1.0104 | 0.3094 | 0.25 | 0.1177 |
| 4 | vwap_above_ratio | vwap | 1.0059 | 1.0 | 0.3 | 0.0768 |
| 5 | r10 | momentum | 0.9252 | 0.8318 | 0.0 | 0.0511 |
| 6 | r15 | momentum | 0.8972 | 0.917 | 0.0 | 0.0649 |
| 7 | r5 | momentum | 0.8763 | 0.6042 | 0.0 | 0.0622 |
| 8 | return_from_open_pct | other | 0.8492 | 0.9434 | -0.2032 | 0.084 |
| 9 | r30 | momentum | 0.7595 | 1.5861 | 0.0 | 0.0467 |
| 10 | day_high_distance | other | -0.7362 | 0.2525 | 1.1595 | 0.0767 |
| 11 | trading_value_rate | volume | 0.5535 | 57593.25 | 37749.02 | 0.0933 |
| 12 | trading_value | volume | -0.4258 | 174874900.0 | 916868600.0 | 0.0482 |
| 13 | consecutive_above_ticks | vwap | 0.3433 | 36.0 | 0.0 | 0.0884 |
| 14 | up_tick_ratio_15m | momentum | 0.2975 | 0.1527 | 0.1138 | 0.0101 |
| 15 | high_update_count_30m | high_update | 0.2086 | 4.0 | 3.0 | 0.0063 |

## Gate Candidates (discovery only — no replay)

- **PB1-vwap_dev_pct** (1 cond): `vwap_dev_pct gt 0.6734` — coverage=0.4912 fp=0.1287 sep=0.3625
- **PB1-vwap_structure_score** (1 cond): `vwap_structure_score gt 2.5473` — coverage=0.4912 fp=0.1887 sep=0.3025
- **PB2-top2** (2 cond): `vwap_structure_score gt 2.5473 AND vwap_dev_pct gt 0.6734` — coverage=0.386 fp=0.0864 sep=0.2996
- **PB1-momentum_continuation_score** (1 cond): `momentum_continuation_score gt 0.3094` — coverage=0.1111 fp=0.0071 sep=0.104
- **PB3-top3** (3 cond): `vwap_structure_score gt 2.5473 AND vwap_dev_pct gt 0.6734 AND momentum_continuation_score gt 0.3094` — coverage=0.0643 fp=0.0 sep=0.0643
- **PB1-vwap_above_ratio** (1 cond): `vwap_above_ratio gt 1.0` — coverage=0.0 fp=0.0 sep=0.0

## Focus symbols (runner days)

- 3441.T 20260618: o2c=5.0553% dh=7.425%
- 7256.T 20260617: o2c=17.6316% dh=18.4211%

**判定:** `pre_breakout_signal_found`