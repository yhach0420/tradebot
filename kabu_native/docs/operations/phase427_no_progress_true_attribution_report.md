# Phase427 — No Progress Exit True Attribution Audit

Generated: 2026-06-17T21:22:04+09:00
Verdict: **adopt_candidate**

## Portfolio comparison (678 evaluated)

| metric | baseline | no_progress | delta |
|--------|----------|-------------|-------|
| total PnL (yen) | 141767.98 | 223688.67 | 81920.69 |
| PF | 1.1352 | 1.2395 | 0.1043 |
| max DD (yen) | 102282.41 | 81231.88 | -21050.53 |
| expectancy | 209.1 | 329.92 | 120.82 |
| affected | — | 183+165- | 348 |

## Reach subset (n=86)

- baseline total: -181941.12 yen
- shadow total: -131738.43 yen
- delta: 50202.69 yen

## Integrity audit

- status: PASS
- post_baseline_violations: 0
- future_mfe_violations: 0

## vs Phase404 / Phase408

- Phase404 uncorrected: +274912.4 yen
- Phase408 corrected: +67872.4 yen
- Phase427 corrected: +81920.69 yen

## 必須回答

- 1_affected_trade_count: 348
- 2_delta_pnl: 81920.69
- 3_delta_pf: 0.1043
- 4_delta_dd: -21050.53
- 5_improved_count: 183
- 6_worsened_count: 165
- 7_expectancy_positive: True
- 8_phase404_difference: Phase404 +274k used uncapped session ticks (lookahead); Phase427 corrected +81921 on Phase423 stream
- 9_adopt_candidate: True
- 10_research_continue: True