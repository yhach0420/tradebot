# Phase400 — Holding Time Audit (Position-CAP Mode)

Generated: 2026-07-12T01:47:52+09:00

Period: `20260615` – `20260615`
Source: Phase399 `phase399_historical_position_cap_backfill_trades.csv` (position_cap_accepted trades only)
Accepted trades analyzed: **64**

## 必須回答

### 1. Position-CAP の平均保有時間

**665s (11.1min)** (`avg_hold_sec=664.55`)

### 2. 中央値

**276s (4.6min)** (`median_hold_sec=276.0`)

補足: p90=1673s (27.9min), p95=2456s (40.9min), max=6366s (1.77h)

### 3. 長時間占有銘柄 Top20（total_cap_seconds 順）

| rank | symbol | trades | total_cap_sec | avg_hold | total_pnl | win_rate |
|------|--------|--------|---------------|----------|-----------|----------|
| 1 | 4062.T | 1 | 6366.0 | 6366.0 | ¥20500.22 | 1.0 |
| 2 | 9984.T | 5 | 5196.0 | 1039.2 | ¥12200.07 | 0.6 |
| 3 | 215A.T | 7 | 4432.0 | 633.14 | ¥-99.88 | 0.5714 |
| 4 | 6613.T | 8 | 4080.0 | 510.0 | ¥5700.5 | 0.5 |
| 5 | 6264.T | 7 | 4079.0 | 582.71 | ¥2900.17 | 0.8571 |
| 6 | 6962.T | 4 | 2983.0 | 745.75 | ¥-1500.08 | 0.5 |
| 7 | 4588.T | 1 | 2553.0 | 2553.0 | ¥-1499.99 | 0.0 |
| 8 | 464A.T | 5 | 2002.0 | 400.4 | ¥-2499.82 | 0.2 |
| 9 | 6323.T | 1 | 1735.0 | 1735.0 | ¥999.91 | 1.0 |
| 10 | 7717.T | 2 | 1676.0 | 838.0 | ¥1000.19 | 0.5 |
| 11 | 7220.T | 2 | 1571.0 | 785.5 | ¥-11999.85 | 0.0 |
| 12 | 3687.T | 4 | 1430.0 | 357.5 | ¥-400.09 | 0.5 |
| 13 | 6976.T | 2 | 1144.0 | 572.0 | ¥-3000.76 | 0.5 |
| 14 | 4047.T | 3 | 825.0 | 275.0 | ¥-10499.9 | 0.3333 |
| 15 | 3905.T | 1 | 623.0 | 623.0 | ¥-1000.17 | 0.0 |
| 16 | 6779.T | 4 | 589.0 | 147.25 | ¥-10000.1 | 0.25 |
| 17 | 4378.T | 2 | 392.0 | 196.0 | ¥700.03 | 1.0 |
| 18 | 6656.T | 1 | 392.0 | 392.0 | ¥200.02 | 1.0 |
| 19 | 6855.T | 1 | 247.0 | 247.0 | ¥1999.79 | 1.0 |
| 20 | 7746.T | 1 | 157.0 | 157.0 | ¥-1099.96 | 0.0 |

### 4. 長時間保有は利益に繋がっているか

**はい（勝ちトレードの平均保有 > 負け）**

- 勝ち平均保有: 902s (15.0min)
- 負け平均保有: 475s (7.9min)
- 勝ち合計PnL: ¥79500.67
- 負け合計PnL: ¥-78800.39

### 5. CAP効率改善余地はあるか

**あり**

- position_cap reject 件数: 90
- 損失トレードの CAP 秒数シェア: 31.3%
- Opportunity Cost 上限推定: ¥984.6 (保守: ¥4501.8)
- pnl / cap-minute: ¥0.9879

### 6. 将来的に時間切れ EXIT 研究が必要か

**推奨**

- session_close 平均保有: 1872.0
- p90+ 長時間負けトレード数: 3

## EXIT 理由別

| bucket | trades | avg_hold | median | p90 | total_pnl | win_rate |
|--------|--------|----------|--------|-----|-----------|----------|
| overlap_replaced | 26 | 349.73 | 139.0 | 924.0 | ¥5499.27 | 0.4231 |
| session_close | 6 | 1872.0 | 1822.0 | 2878.5 | ¥23200.31 | 0.6667 |
| stop_hit | 15 | 419.07 | 198.0 | 1137.0 | ¥-63599.69 | 0.0 |
| trailing_mfe | 17 | 936.47 | 271.0 | 1895.4 | ¥35600.39 | 1.0 |

## CAP 占有時間 Top20（単一トレード hold_sec 順）

| rank | symbol | hold_sec | pnl | exit | winner |
|------|--------|----------|-----|------|--------|
| 1 | 4062.T | 6366.0 | ¥20500.22 | trailing_mfe | True |
| 2 | 9984.T | 3204.0 | ¥16700.11 | session_close | True |
| 3 | 6264.T | 2787.0 | ¥1500.01 | trailing_mfe | True |
| 4 | 4588.T | 2553.0 | ¥-1499.99 | session_close | False |
| 5 | 215A.T | 1909.0 | ¥-500.0 | session_close | False |
| 6 | 6323.T | 1735.0 | ¥999.91 | session_close | True |
| 7 | 6613.T | 1730.0 | ¥-4399.92 | stop_hit | False |
| 8 | 464A.T | 1539.0 | ¥700.07 | overlap_replaced | True |
| 9 | 6962.T | 1505.0 | ¥499.98 | overlap_replaced | True |
| 10 | 7220.T | 1359.0 | ¥-5999.9 | stop_hit | False |
| 11 | 215A.T | 1301.0 | ¥400.0 | trailing_mfe | True |
| 12 | 6613.T | 1071.0 | ¥5500.0 | session_close | True |
| 13 | 3687.T | 1004.0 | ¥1199.92 | trailing_mfe | True |
| 14 | 9984.T | 932.0 | ¥4599.93 | overlap_replaced | True |
| 15 | 7717.T | 916.0 | ¥-1000.09 | overlap_replaced | False |
| 16 | 6976.T | 863.0 | ¥4500.06 | trailing_mfe | True |
| 17 | 6962.T | 804.0 | ¥-1200.03 | stop_hit | False |
| 18 | 7717.T | 760.0 | ¥2000.28 | session_close | True |
| 19 | 6264.T | 693.0 | ¥700.04 | trailing_mfe | True |
| 20 | 215A.T | 662.0 | ¥199.97 | overlap_replaced | True |

## Opportunity Cost 推定

- reject 件数 × 平均 accepted PnL（上限）: ¥984.6
- reject 件数 × 中央値 accepted PnL（保守）: ¥4501.8
- p90+ 長時間負けの CAP 秒数: 6192.0
- 短時間勝ち（≤median）PnL: ¥8799.88

## 成果物

- `results/reports/phase400_holding_time_summary.json`
- `results/reports/phase400_holding_time_by_exit_reason.csv`
- `results/reports/phase400_symbol_holding_time.csv`
- `results/reports/phase400_cap_occupation_top20.csv`
