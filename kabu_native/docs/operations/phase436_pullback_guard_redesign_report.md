# Phase436 — Pullback Guard Redesign (Shadow)

Generated: 2026-06-18T21:20:39+09:00
**Verdict:** `high_drift_candidate`

## Comparison

| guard | trades | PnL | PF | stop_rate | maxDD | 6976 removal |
|-------|--------|-----|-----|-----------|-------|--------------|
| baseline | 810 | 47,568 | 1.0367 | 0.2716 | 158,700 | 0/44 (0.0) |
| legacy_vwap_pullback | 810 | 47,568 | 1.0367 | 0.2716 | 158,700 | 0/44 (0.0) |
| high_drift | 771 | 138,567 | 1.124 | 0.2672 | 102,282 | 7/37 (0.1892) |
| momentum_window | 788 | 120,468 | 1.1013 | 0.2665 | 102,282 | 2/42 (0.0476) |
| near_recent_low | 808 | 48,068 | 1.0372 | 0.2723 | 158,700 | 0/44 (0.0) |
| trend_slope | 796 | 130,668 | 1.1091 | 0.2663 | 102,282 | 2/42 (0.0476) |

## Guard definitions (shadow)

| guard | rule (dynamic40 only) |
|-------|-------------------------|
| high_drift | A: day_high≥1.2%, r10<-0.15%, r5>r10 (small bounce). B: day_high≥1.5%, r15<-0.5% or r5<-0.5% (sustained decline) |
| momentum_window | r15<-0.3% or r30<-0.45% with r5≥-0.15% (weak trend + short bounce) |
| near_recent_low | within 0.5% of 30m low, r30<0, r5≥0 |
| trend_slope | 30m slope <-0.015%/min with r5≥0 |
| legacy_vwap | rise5<0 AND vwap_dev<0 (Phase355) |

## 6976 on 2026-06-18 (case study)

- 2026-06-18T09:20:16+09:00: pnl=10,000, exit=trailing_mfe, blocked=['high_drift']
- 2026-06-18T09:25:41+09:00: pnl=1,500, exit=trailing_mfe, blocked=['high_drift']
- 2026-06-18T09:37:12+09:00: pnl=-36,000, exit=stop_hit, blocked=['high_drift']
- 2026-06-18T10:02:27+09:00: pnl=-27,000, exit=stop_hit, blocked=['high_drift', 'momentum_window', 'trend_slope']
- 2026-06-18T12:49:29+09:00: pnl=5,500, exit=trailing_mfe, blocked=none

## Notes

- VWAP-free guards target dynamic40 downtrend + small bounce (6976 pattern).
- Baseline: Phase423 canonical + forward capital sim accepted trades.
- Runtime change forbidden; shadow replay only.
