# kabu_native Universe 管理

**対象:** `kabu_native/src/universe/`、`kabu_native/scripts/build_universe.py`

## 目的

旧系の固定 `watchlist.json` ではなく、**流動性・価格帯・スプレッド・市場区分** で監視候補を絞り込むための銘柄集合（universe）を管理します。

- 候補は `include_symbols` から出発し、**kabu `/board` の実データ**でフィルタする。
- 通過・除外と **除外理由** を CSV / JSON に残す。
- 成果物は `kabu_native/data/universe/` に保存（旧 `watchlist.json` は変更しない）。

## 旧 watchlist との違い

| 項目 | 旧系 | kabu_native universe |
|------|------|----------------------|
| 正の銘柄リスト | ルート `watchlist.json` | `data/universe/universe_YYYYMMDD.{csv,json}` |
| 選定 | 手動・Discord `!watch` | YAML 閾値 + board 実測 |
| データ源 | Yahoo（監視ループ） | kabu REST `/board` |
| paper_trade / watchdog | 従来どおり旧 watchlist | **影響なし** |

## 設定（`configs/universe.yaml`）

| キー | 型 | 説明 |
|------|-----|------|
| `market` | string | 市場区分。`prime` = 東証プライム（`ExchangeName` に「プ」等） |
| `include_symbols` | list | 板取得の対象（`9984` / `9984.T` / `9984@1`） |
| `exclude_symbols` | list | 設定上の除外（board 取得前でも理由に記録） |
| `exclude_etf` | bool | ETF / 非株式 `SecurityType` / 名称ヒューリスティックで除外 |
| `min_trading_value` | number \| null | `TradingValue`（円）下限 |
| `min_trading_volume` | number \| null | `TradingVolume`（株）下限（任意） |
| `min_price` | number \| null | `CurrentPrice` 下限 |
| `max_price` | number \| null | `CurrentPrice` 上限 |
| `max_spread_bps` | number \| null | 最良気配スプレッド上限（bps） |
| `max_symbols` | int \| null | 通過銘柄数上限（`TradingValue` 降順） |
| `default_exchange` | int | 市場コード省略時の既定（東証=1） |

## 銘柄コード（`symbols.py`）

| 入力 | 正規化 |
|------|--------|
| `9984` | code=`9984`, exchange=既定(1) |
| `9984.T` | code=`9984`, exchange=既定 |
| `9984@1` | code=`9984`, exchange=`1` |

kabu API: `symbol_key` = `9984@1`（`GET /board/9984@1`）。

## フィルタ（`filters.py`）

board レスポンスから算出・判定:

| 指標 | board フィールド |
|------|------------------|
| 価格 | `CurrentPrice`（欠損時 `CalcPrice`） |
| 売買代金 | `TradingValue` |
| 売買高 | `TradingVolume` |
| スプレッド | `BidPrice`, `AskPrice` → bps |
| 銘柄種別 | `SecurityType` |
| 市場 | `Exchange`, `ExchangeName` |

### 除外理由コード（例）

| 理由 | 意味 |
|------|------|
| `config_exclude_symbols` | `exclude_symbols` に一致 |
| `board_fetch_error` | API エラー |
| `market_not_prime` | プライム以外 |
| `etf` | ETF 判定 |
| `security_type_not_equity` | 株式以外の SecurityType |
| `trading_value_below_min` | 売買代金不足 |
| `trading_volume_below_min` | 売買高不足 |
| `price_below_min` / `price_above_max` | 価格帯外 |
| `spread_bps_above_max` | スプレッド過大 |
| `missing_*` | 判定に必要な値が null |
| `max_symbols_cap` | `max_symbols` 超過で落選 |

## 実行例

リポジトリルートで（kabuステーション起動・`.env` に `KABU_API_PASSWORD`）:

```bash
pip install PyYAML   # 未導入時

python kabu_native/scripts/build_universe.py --config kabu_native/configs/universe.yaml
```

### 出力

| ファイル | 内容 |
|----------|------|
| `kabu_native/data/universe/universe_YYYYMMDD.csv` | 全候補 1 行（`exclude_reasons` 列付き） |
| `kabu_native/data/universe/universe_YYYYMMDD.json` | `included` / `excluded` / `rows` / 設定スナップショット |
| `logs/runtime/kabu_native_build_universe_YYYYMMDD.log` | 実行ログ（トークンなし） |

## 今後の拡張

- 東証銘柄マスタからの `include_symbols` 自動生成
- 朝スクリーニング（Phase 3）への universe 入力
- PUSH 登録銘柄との突合

## 関連

- [api_layer.md](api_layer.md) — REST クライアント
- [architecture.md](architecture.md) — 全体フェーズ
