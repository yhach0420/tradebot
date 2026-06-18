# Phase425 — 20260617 PM Drawdown Attribution

Generated: 2026-06-17T20:26:39+09:00
Verdict: **cap5_confirmed**

## Equity path

- 20260616 end: 1,641,767.98
- 20260617 AM: 1,668,067.98 (+26,300)
- 20260617 PM: 1,645,767.98 (-22,300 vs AM)

## 必須回答

1. **PM損失上位5銘柄**: 6976.T, 5016.T, 3915.T, 5367.T, 186A.T
2. **CAP5追加案件数**: 9
3. **CAP5追加案件PnL合計**: -20400.0 yen
4. **CAP3との差 (PM)**: 1600.0 yen (CAP5 - CAP3)
5. **CAP5が悪化要因か**: No — CAP3 PM was worse (-23,900 vs -22,300)
6. **CAP5維持推奨か**: Yes
7. **PM損失の性質**: session_stop_cluster_not_cap5_structural
8. **次に監視すべき銘柄**: 6976.T, 5016.T, 3915.T, 6966.T, 186A.T

## PM loss top 5 trades

- 6976.T 2026-06-17T13:11:45+09:00: -27000.0 yen (stop_hit) incremental=True
- 5016.T 2026-06-17T13:10:58+09:00: -5700.0 yen (stop_hit) incremental=False
- 3915.T 2026-06-17T13:16:22+09:00: -3600.0 yen (stop_hit) incremental=False
- 5367.T 2026-06-17T13:11:27+09:00: -1900.0 yen (stop_hit) incremental=False
- 186A.T 2026-06-17T13:11:47+09:00: -1700.0 yen (stop_hit) incremental=True

## CAP5 incremental PM

- count: 9
- total PnL: -20400.0 yen
- mean: -2266.67 yen

## CAP3 vs CAP5 PM

- CAP3 PM PnL: -23900.0 yen (14 accepted)
- CAP5 PM PnL: -22300.0 yen (21 accepted)

## Outputs

- `results/reports/phase425_pm_drawdown_attribution.csv`
- `results/reports/phase425_cap3_vs_cap5_20260617pm.csv`
- `results/reports/phase425_cap5_incremental_positions.csv`
- `results/reports/phase425_pm_drawdown_summary.json`
