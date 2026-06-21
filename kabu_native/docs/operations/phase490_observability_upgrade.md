# Phase490 — Observability Upgrade

Implemented: C01 Symbol Attribution, C02 Exit Breakdown, C03 Runtime Health,
C05 stop_low_mfe counter/tag, C06 Reject Funnel.

No Entry/Exit/Gate/Runtime logic changes — Discord formatting only.

## Before / After

Mock JSON: `results/reports/phase490_discord_mockups.json`

### Daily Summary (before)

```
trade_count: 3
win_rate_yen_100: 33%
profit_factor_yen_100: 0.909
total_pnl_yen_100: -1,000円(100株)
avg_pnl_yen_100: -333円/取引(100株)
stop_rate: 67%
best_trade: 6976 +10,000円(100株) +0.50% (利益確定条件到達)
worst_trade: 6976 -10,000円(100株) -0.50% (損切りライン到達)
max_concurrent: 5/5
監視銘柄数: 120
取引銘柄数: 2
```

### Daily Summary (after)

```
trade_count: 3
win_rate_yen_100: 33%
profit_factor_yen_100: 0.909
total_pnl_yen_100: -1,000円(100株)
avg_pnl_yen_100: -333円/取引(100株)
stop_rate: 67%
best_trade: 6976 +10,000円(100株) +0.50% (利益確定条件到達)
worst_trade: 6976 -10,000円(100株) -0.50% (損切りライン到達)
max_concurrent: 5/5
監視銘柄数: 120
取引銘柄数: 2
```

**Symbol Attribution**
```
4062 イビデン: -1,000円(100株) (1T, 100% of day) ⚠
6976 太陽誘電: +0円(100株) (2T, -0% of day)
top3_share: 100%
```
**Exit Breakdown**
```
stop_hit: 2 (-11,000円(100株))
trailing_mfe: 1 (+10,000円(100株))
stop_low_mfe: 2 (-11,000円(100株))
```
**Runtime Health**
```
api_errors: 1
stale_ticks: 3089
data_gaps: 38
feature_complete: 94.8%
config: …3c45
peak_slots: 5/5
```
**Reject Funnel**
```
data_stale_price: 31901
high_drift_pullback: 4385
max_concurrent: 1658
late_chase_guard: 12
```

### HEARTBEAT (before → after)

Before: runtime_sec, api_errors, stale_ticks only.

After adds:

```
data_gaps: 38
feature_complete: 94.8%
config: …3c45
peak_slots: 5/5
```

### ENTRY (unchanged — reference mock)

```
銘柄: 6976.T 太陽誘電
時刻: 09:12:34
ENTRY価格: 19955.00
損切り価格: 19700.00
保有枠: 3/5
entry_score_v2: 4
ENTRY理由:
・モメンタムが相対的に低い（スコア加点）
ENTRY条件を満たしたためエントリー
```

### EXIT (after — stop_low_mfe tag)

```
銘柄: 4062.T イビデン
EXIT時刻: 10:05:00
ENTRY価格: 1000.00
EXIT価格: 990.00
損益: -1.00% / -1,000円(100株)
最大含み益 MFE: 0.20%
最大逆行 MAE: -1.00%
保有時間: 8分
EXIT理由: 損切りライン到達
⚠ stop_low_mfe: MFE<0.5% at stop
```
