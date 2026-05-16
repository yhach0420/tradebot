# Phase 11: morning_screen × replay 統合検証

## 目的

A+B シグナル/EXIT の上で、**監視銘柄（screen 上位 N）** が trade 品質を改善するか検証。

期間: 2026-04-10 〜 2026-05-15（27 銘柄）

Screen 方式: **Walk-forward top-N by prior-day intraday turnover (morning_screen liquidity proxy). score_proxy = normalized avg turnover.**

## リプレイ比較（A+B 固定）

| scenario | top_n | trades | total_pnl | avg_pnl | PF | MFE≥0.3% | 9984 share | large_cap% | trades/sym |
|----------|-------|--------|-----------|---------|-----|-----------|------------|------------|------------|
| universe_full | 27 | 46 | -28.81% | -0.626% | 0.075 | 21.7% | 0.2252 | 80.4% | 4.60 |
| screen_top_5 | 5 | 37 | -10.01% | -0.271% | 0.190 | 21.6% | 0.5135 | 100.0% | 12.33 |
| screen_top_10 | 10 | 38 | -10.03% | -0.264% | 0.189 | 21.1% | 0.5129 | 97.4% | 9.50 |
| screen_top_15 | 15 | 42 | -22.73% | -0.541% | 0.093 | 23.8% | 0.2752 | 88.1% | 7.00 |

## Screen ランキング（期間平均 turnover プロキシ）

| rank | symbol | score | avg_turnover |
|------|--------|-------|--------------|
| 1 | 9984.T | 100.0 | 523467425890 |
| 2 | 5803.T | 86.2993 | 451748674235 |
| 3 | 6857.T | 73.7569 | 386093314650 |
| 4 | 6920.T | 59.2824 | 310324055600 |
| 5 | 5016.T | 38.4148 | 201088934395 |
| 6 | 7974.T | 22.5982 | 118294296295 |
| 7 | 7011.T | 22.4008 | 117260886520 |
| 8 | 8306.T | 21.2711 | 111347019528 |
| 9 | 6501.T | 18.9908 | 99410649550 |
| 10 | 8058.T | 16.1895 | 84746542035 |
| 11 | 7013.T | 15.3445 | 80323590595 |
| 12 | 7203.T | 13.1954 | 69073411700 |
| 13 | 7012.T | 11.6214 | 60834505145 |
| 14 | 8031.T | 10.9483 | 57310748360 |
| 15 | 8002.T | 10.6102 | 55541008410 |

## score と pnl

- Pearson(score, pnl) universe_full: **0.615275725347783**
- Pearson(rank, pnl) universe_full: **-0.4858692164701845**
- 上位N trade の質: top_rank_win_rate / pnl は各 scenario の `bias_*` 参照

## 結論

| 質問 | 結果 |
|------|------|
| morning_screen（流動性 proxy）で trade 品質改善？ | **はい** — top5/10 で total_pnl **-10%** vs universe **-28.8%**、PF **0.19** vs **0.075** |
| universe 全体より screen 後が良い？ | **はい**（walk-forward top5/10 が最良） |
| 9984 偏重は減る？ | **いいえ** — screen 上位は 9984/5803 等の超高流動性銘柄が中心（9984 share **51%** vs universe **23%**） |
| score と pnl | **正の相関**（Pearson **+0.62**）— 高 score（高 turnover）銘柄で損失が相対的に小さい |
| rank と pnl | **負の相関**（**-0.49**）— rank1=9984 は依然として損失寄与大 |

### paper_trade shadow へ進める条件

**整った** — 共通ルール bundle:

- **Signal/EXIT**: Phase 10 `A_plus_B`（09:30 ゲート + BF confirm=2 + buffer 0.12）
- **Watchlist**: `walk_forward_top_10`（前日までの intraday turnover で上位10、27銘柄中）

trade 数 38（フロア25超）、PF・total_pnl が universe より改善。  
**注意**: 流動性 screen は大型・超高流動性バイアスが強い。live `run_morning_screen.py` との差分は本番 shadow で要確認。

- screen が universe より良い: **True**
- walk-forward top10 が universe より PnL 改善: **True**
- 9984 偏重が減った: **False**
- paper_trade shadow 準備: **True**
- 推奨 watchlist: **walk_forward_top_10**

Screen rank uses intraday turnover proxy (walk-forward) when live morning_screen is not available per backtest day. Score~normalized avg turnover.

## 出力

- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase11_screen_replay_20260517.csv`
- `C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports\phase11_screen_replay_20260517.json`
