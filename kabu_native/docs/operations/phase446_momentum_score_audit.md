# Phase446 — Momentum Score Source Audit

Generated: 2026-06-19T21:09:26+09:00
Verdict: **momentum_misclassification**
Target day: 20260619

## Part A — Definition location

- file: `src/small_paper/live_feature_bridge.py`
- function: `LiveFeatureBridge._momentum_score` (lines 272–295)
- entry gate: `src/small_paper/entry_expectancy_score_shadow.py`

## Part B — Formula

- weights: {'price_mom': 0.4, 'vwap_part': 0.25, 'mfe_proxy': 0.35}
- formula: `momentum_continuation_score = clip(0.40*price_mom + 0.25*vwap_part + 0.35*mfe_proxy, 0, 1)`
- inputs:
  - price_mom: min(1, max(0, (price-p0)/p0 / 0.008)) over momentum_lookback=5 ticks
  - vwap_part: min(1, max(0, 0.5 + (price-vwap)/vwap / 0.004))
  - mfe_proxy: min(1, max(0, (rolling_mfe - 0.4*abs(rolling_mae)) / 0.35))

## Part C — Normalization

ENTRY gate uses fixed Phase229 tertile cutoffs (not session percentile). Classification: val<=p33->low, p33<val<=p66->mid, else high.

## Part D — Tertile cutoffs

- fixed Phase229: p33=0.2546, p66=0.2988
- session candidate p33/p66: {'p33': 0.0002, 'p66': 0.0792}
- session accepted p33/p66: {'p33': 0.0037, 'p66': 0.0678}

## Part E/F — Top trade decomposition

See `phase446_momentum_score_audit.csv` (loss_top10 / win_top10).

## Mandatory answers

1. 定義: LiveFeatureBridge._momentum_score: weighted blend of 5-tick price momentum, VWAP distance, and rolling MFE/MAE proxy on live PUSH ticks
2. 入力: ['pure_price_momentum (5-tick lookback)', 'entry_vwap_dev_pct / VWAP distance', 'rolling_mfe_pct', 'rolling_mae_pct']
3. 式: momentum_continuation_score = clip(0.40*price_mom + 0.25*vwap_part + 0.35*mfe_proxy, 0, 1)
4. Momentum:low: momentum_continuation_score <= 0.2546 (Phase229 fixed p33 tertile); required token for ENTRY (Momentum:low +2 pts)
5. 上昇継続評価: Partially — uses short tick-window price rise + VWAP premium + intraday MFE; does NOT use r15/r30 or day-high distance directly
6. 下落反発誤認: True
7. 勝敗差: {'winner_avg_momentum': 0.0704, 'loser_avg_momentum': 0.0591, 'material_difference': False}
8. 改善余地: True
9. Momentum vs Universe: momentum_misclassification
10. 次修正候補: Add 15m/30m drift + day-high distance to momentum score or gate; tighten fallback-path entries (quality_fallback_path=true → score≈0)

## Session stats

- closed: 128, PnL: -232700.0 yen (100 shares)
- winners/losers: 61/64
- all momentum low: True
- fallback accepts: 11
