# Phase476 — Pre-Breakout Gate Replay Audit

**Verdict:** `pre_breakout_overfit`
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良Gate | **PB5 (vwap_above_ratio >= 0.7 AND vwap_dev_pct > 0.6734)** |
| 2 | 最良Exit | **C (Session Hold (Hard Stop only → session close))** |
| 3 | PB only PnL | **407600.0** |
| 4 | PB only PF | **1.6154** |
| 5 | PBv2 OR PB PnL | **198664.35** |
| 6 | PBv2破壊 | **True** |
| 7–10 | 3441/6492/7256/7600 | False/False/False/False |
| 11 | 6976 | {'pbv2': 221001.28, 'dual': 319500.93, 'pb_only': 568000.0} |
| 12 | 4062 | {'pbv2': 9001.55, 'dual': -4496.56, 'pb_only': -23000.0} |
| 13 | same-tick/zero | {'zero_exit_count': 0, 'same_tick_exit_count': 0, 'exit_within_5_ticks_count': 2} |
| 14 | 過学習 | **True** |
| 15 | Runtime候補 | **False** |
| 16 | Shadow候補 | **None** |
| 17 | 次アクション | Verdict: pre_breakout_overfit; Best: PB5 × Exit C; Extend period or tighten gates; do not runtime wire; Runtime candidate: False; overfit: True |

## Gate × Exit (top 10 by PnL)

| gate | exit | PnL | PF | acc | same-tick | ≤5tick |
|---|---|---:|---:|---:|---:|---:|
| PB5 | C | 407600.0 | 1.6154 | 151 | 0 | 2 |
| PB5 | A | 185188.06 | 1.3641 | 144 | 1 | 5 |
| PB4 | C | 69790.0 | 1.1761 | 102 | 5 | 7 |
| PB1 | C | 63200.0 | 1.0821 | 177 | 5 | 8 |
| PB3 | B | 50819.82 | 1.1329 | 919 | 1 | 905 |
| PB3 | C | 31290.0 | 1.0867 | 85 | 0 | 3 |
| PB2 | C | 23000.0 | 1.0429 | 119 | 0 | 3 |
| PB4 | B | -20079.8 | 0.9696 | 1732 | 6 | 1715 |
| PB4 | A | -30160.16 | 0.8933 | 50 | 6 | 9 |
| PB3 | A | -42859.92 | 0.8499 | 35 | 1 | 2 |

**判定:** `pre_breakout_overfit`