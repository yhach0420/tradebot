# kabu_native 朝スクリーニング

**対象:** `kabu_native/src/screening/morning_screen.py`、`kabu_native/scripts/run_morning_screen.py`

## 目的

`universe_YYYYMMDD.csv` で **passed=true** の銘柄だけを対象に、kabu `/board` の実データでスコアリングし、当日の監視候補をランキングします。

- **寄り前 / 寄り直後 / 引け後** いずれでも実行可能（`session_mode: any`）
- 旧系 `yahoo_kabu_watch.py --morning-screen` とは **別パイプライン**（`watchlist.json` は変更しない）

## 入力

| 入力 | 説明 |
|------|------|
| `kabu_native/data/universe/universe_YYYYMMDD.csv` | Phase 2 の universe 成果物（`passed=true` 行のみ） |
| kabu REST `/board/{symbol@exchange}` | 実行時に再取得 |

## スコア項目（10）

| # | 項目 | 元データ | 概要 |
|---|------|----------|------|
| 1 | TradingValue | `TradingValue` | バッチ内 min-max 正規化 |
| 2 | TradingVolume | `TradingVolume` | 同上 |
| 3 | CurrentPrice | `CurrentPrice` | 価格帯ゲート内なら高得点 |
| 4 | ChangePreviousClosePer | `ChangePreviousClosePer` | +1〜5% を高評価（旧系に近い帯） |
| 5 | VWAP乖離 | `(price-vwap)/vwap` | VWAP 上を加点 |
| 6 | HighPrice接近率 | `price/HighPrice` | 高値 98% 以上で満点付近 |
| 7 | spread_bps | `BidPrice`/`AskPrice` | 狭いほど高得点 |
| 8 | board_imbalance | 気配数量 | 買い厚み比率（ロングバイアス） |
| 9 | freshness | `CurrentPriceTime` | 鮮度（古いほど低得点、0にはしない） |
| 10 | SecurityType | `SecurityType` | 株式(1) を前提（ゲート） |

重みは `configs/morning_screen.yaml` の `weights` で調整。

## 出力

`kabu_native/results/morning_screen/YYYYMMDD/morning_screen_YYYYMMDD_HHMMSS.{csv,json}`

### CSV 列

`rank`, `symbol`, `symbol_name`, `current_price`, `change_pct`, `trading_value`, `trading_volume`, `vwap`, `vwap_distance_pct`, `high_proximity_ratio`, `spread_bps`, `board_imbalance`, `freshness_sec`, `score`, `pass_screen`, `reject_reasons`

- **null は空欄のまま出力**（行は落とさない）
- `reject_reasons` に `missing_*` やゲート違反を記録
- `rank` は **pass_screen=true** かつ `max_symbols` 内の上位のみ付与

### JSON

- `meta` — 実行メタ
- `config` — 設定スナップショット
- `top` — ランク付き上位
- `rows` — 全評価行（`output_all_rows: true` 時）

## 設定（`configs/morning_screen.yaml`）

| キー | 説明 |
|------|------|
| `session_mode` | `any` = 鮮度で hard reject しない（引け後可） |
| `weights` | 各サブスコアの重み |
| `gates` | `pass_screen` 判定（閾値外は `reject_reasons`） |
| `max_symbols` | ランク付き上位件数 |
| `output_all_rows` | false なら top のみ CSV |

### reject_reasons 例

| 理由 | 意味 |
|------|------|
| `missing_trading_value` | 値が null（行は残す） |
| `board_fetch_error` | API 失敗 |
| `change_pct_above_max` | 急騰しすぎ |
| `spread_bps_above_max` | スプレッド過大 |
| `security_type_not_equity` | 非株式 |

## 実行例

```bash
# 1. universe ビルド（Phase 2）
python kabu_native/scripts/build_universe.py --config kabu_native/configs/universe.yaml

# 2. 朝スクリーニング
python kabu_native/scripts/run_morning_screen.py \
  --universe kabu_native/data/universe/universe_20260516.csv \
  --config kabu_native/configs/morning_screen.yaml
```

## 旧 watchlist / 旧朝スクリーニングとの違い

| 項目 | 旧系 | kabu_native |
|------|------|-------------|
| 銘柄母集団 | `symbols.csv` / `watchlist.json` | universe CSV（board フィルタ済） |
| データ | Yahoo | kabu `/board` |
| 出力 | ターミナル / Discord | `results/morning_screen/` |
| 実行タイミング | 寄り前想定 | **any**（引け後も可） |

## 関連

- [universe.md](universe.md)
- [api_layer.md](api_layer.md)
