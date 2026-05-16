# kabu `/board` と既存 bot（`yahoo_kabu_watch.py` / paper_trade）の項目対応（Phase 2）

本書は、kabuステーション API の **GET `/board/{symbol}`（スキーマ `BoardSuccess`）** が返す項目と、現行コードが **Quote / 日中シグナル / VWAP / 補助時系列** に求める項目の対応を整理します。

- **参照OpenAPI**: [kabu_STATION_API.yaml](https://github.com/kabucom/kabusapi/blob/master/reference/kabu_STATION_API.yaml) の `BoardSuccess`
- **実行時のキー一覧**: `python scripts/kabu_api_check.py --symbol 9984` の結果 JSON  
  - **`_note_response_keys`**: 実レスポンスに存在したトップレベルキー（銘柄・市場・セッションにより欠損あり）  
  - **`_board_openapi_boardsuccess_top_level_keys`**: 上記スクリプトが保持する OpenAPI 上のトップレベルキー一覧  
  - **`_compare_schema_vs_response`**: スキーマ定義と実レスポンスの集合差分

## 1. `/board`（BoardSuccess）トップレベルキー一覧

`scripts/kabu_api_check.py` が `BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS` として出力する定義に準拠します（子オブジェクトは文末に補足）。

| キー | 概要（OpenAPI上的な意味） |
|------|---------------------------|
| Symbol, SymbolName | 銘柄コード・銘柄名 |
| Exchange, ExchangeName | 市場コード・市場名 |
| CurrentPrice, CurrentPriceTime, CurrentPriceChangeStatus, CurrentPriceStatus | 現値・時刻・騰落区分・ステータス |
| CalcPrice | 計算用現値 |
| PreviousClose, PreviousCloseTime | 前日終値・日付 |
| ChangePreviousClose, ChangePreviousClosePer | 前日比・騰落率(%) |
| OpeningPrice, OpeningPriceTime | 始値・時刻 |
| HighPrice, HighPriceTime | 高値・時刻 |
| LowPrice, LowPriceTime | 安値・時刻 |
| TradingVolume, TradingVolumeTime | 売買高・時刻 |
| VWAP | セッション VWAP（定義は API 仕様に従う） |
| TradingValue | 売買代金 |
| BidQty, BidPrice, BidTime, BidSign | 最良売気配（※API注釈: Bid/Ask ラベルと日本語「買気配/売気配」の対応に注意） |
| AskQty, AskPrice, AskTime, AskSign | 最良買気配 |
| MarketOrderSellQty / MarketOrderBuyQty | 売買成行数量 |
| Sell1…Sell10, Buy1…Buy10 | 気配10段（各オブジェクトに Price, Qty, Time, Sign 等） |
| OverSellQty, UnderBuyQty | OVER/UNDER 気配数量 |
| TotalMarketValue | 時価総額 |
| ClearingPrice | 清算値（主に先物） |
| IV, Gamma, Theta, Vega, Delta | オプション用 |
| SecurityType | 銘柄種別コード |

**気配オブジェクト（Sell*n* / Buy*n*）** は API 仕様上、主に `Price` / `Qty` / `Time` / `Sign` を持ちます（銘柄種別により項目が `null` になり得ます）。

## 2. 既存コードが paper_trade で使うデータ経路

`run_paper_trade` 内では、各銘柄について次を取得しています（実装は `fetch_latest_intraday_data_for_paper_trade` 周辺および同ループ）。

| レイヤ | 主な生成元 | paper_trade での主な利用 |
|--------|------------|---------------------------|
| **Quote** (`fetch_quote` または `KabuProvider.get_quote`→`Quote`) | MARKET_DATA_PROVIDER に依存 | スクリーニング（前日比・高値近辺・売買高等）、価格入力 |
| **MA25** (`fetch_ma25`) | Yahoo chart（日足系） | 「MA25以下」除外 |
| **VWAPスカラー** (`fetch_vwap`) | Yahoo chart（1分足 OHLCV の配列から算出／または配列内 vwap） | `calc_intraday_signals_from_series` へ入力 |
| **1分足系列** (`fetch_intraday_1m_series`) | Yahoo chart（1m, 1d） | highs/closes/volumes → `IntradaySignals` |
| **IntradaySignals** (`calc_intraday_signals_from_series`) | 上記の合成 | **`recent_5m_high`** をエントリー参考価格に、`vwap_distance_pct` をログ列に出力 |

通常監視モードなど paper_trade 以外では、`Quote` や `_LATEST_INTRADAY_SIGNALS`、出来高 spike 判定（avg5）等に同様の分解能が必要ですが、本章では **paper_trade 実装ブロックを主対象** とします。

## 3. 対応表（bot 側項目 → kabu）

**凡例**

- **すぐ置換可能**: `/board` のフィールドをそのまま（または単純キャストで）既存変数に載せ替え可能  
- **加工すれば置換可能**: 単位・符号・欠損時の扱い・Yahoo との定義差の吸収が必要  
- **kabu APIだけでは不足**: 当日スナップショット以外の履歴・配列が必要で `/board` 単体では足りない  
- **Yahoo維持が妥当**: 仕様上・整合性上、Yahoo（または別系列API）のままが安全

### 3.1 `Quote`（dataclass）と paper_trade スクリーニング

| 既存bot項目名 | 現在の取得元 | kabu `/board` フィールド | 置換可能か | 分類 | 注意点 |
|---------------|-------------|--------------------------|------------|------|--------|
| `Quote.price` | Yahoo v7/chart または Kabu（Phase1） | `CurrentPrice`（欠損時 `CalcPrice`） | はい | すぐ置換可能 | 既に `KabuProvider` で `CurrentPrice`/`CalcPrice` を使用 |
| `Quote.previous_close` | Yahoo | `PreviousClose` | はい | すぐ置換可能（欠損時は不可） | `null` のとき Yahoo フォールバック等の運用検討 |
| `Quote.change_percent` | Yahoo（`previousClose` と `price` から算出）または同値相当 | **`ChangePreviousClosePer`** で直接、`PreviousClose` と `CurrentPrice` から再算出 | はい（推奨は再算出か API 値の単一ソース化） | 加工すれば置換可能 | Yahoo と API の丸め・定義が完全一致とは限らない。どちらかに統一方針を決める |
| `Quote.day_high` | Yahoo | `HighPrice` | はい | すぐ置換可能（欠損時は不可） | 日中更新遅延・特別気配時の interpret |
| `Quote.day_low` | Yahoo | `LowPrice` | はい | すぐ置換可能（paper は未使用だが Embed 等他用途あり） | 同上 |
| `Quote.volume` | Yahoo | `TradingVolume` | はい | すぐ置換可能（欠損時は不可） | 単位・丸めが Yahoo と異なる可能性（比較ロジックの再検証） |
| `Quote.market_time_utc` | Yahoo | `CurrentPriceTime`（ISO 文字列→UTCへ変換） | はい | 加工すれば置換可能 | タイムゾーン明示を前提にパース |
| `Quote.currency` | Yahoo メタ | 実質 **`JPY` 前提** で固定または API の market コンテキスト | 一部 | 加工すれば置換可能 | Quote モデルとの整合 |
| `Quote.market_cap` | Yahoo | **`TotalMarketValue`**（株式で返る場合） | 条件付き | 加工すれば置換 possible | Yahoo の `marketCap` と算出基準が同一とは限らない。欠損時は None |

### 3.2 `fetch_vwap`（Yahoo 1分足ベースのスカラー）

| 既存bot項目名 | 現在の取得元 | kabu `/board` フィールド | 置換可能か | 分類 | 注意点 |
|---------------|-------------|--------------------------|------------|------|--------|
| `fetch_vwap` の戻り値（概算VWAP） | Yahoo 1m（配列の vwap か典型価格×出来高の累積） | **`VWAP`** | 条件付き | 加工すれば置換可能 | **定義差**: Yahoo 側は「配列からの推定」、kabu は API 定義のセッション VWAP。数値が一致するとは限らない |
| `IntradaySignals.vwap` | 上記 `fetch_vwap` | 同上 | 条件付き | 加工すれば置換可能 | **現値を kabu・VWAP を Yahoo のまま**だと乖離率の解釈が二重系になる。片方に寄せる方針が必要 |
| `IntradaySignals.vwap_distance_pct` | `price` と `vwap` から算出 | （間接的に）`CurrentPrice` と `VWAP` | 条件付き | 加工すれば置換可能 | 上と同じく **単一データソースに揃える**と安全 |

### 3.3 `fetch_intraday_1m_series` と `IntradaySignals`（配列ベース）

| 既存bot項目名 | 現在の取得元 | kabu `/board` フィールド | 置換可能か | 分類 | 注意点 |
|---------------|-------------|--------------------------|------------|------|--------|
| 1分 `closes[]` / `highs[]` / `vols[]` | Yahoo chart `interval=1m&range=1d` | **なし（時系列配列は返さない）** | いいえ | **kabu APIだけでは不足** | `/board` はスナップショット。直近5本の高値・出来高比較には **履歴配列** が必要 |
| `IntradaySignals.recent_5m_high` | 1分 `highs` から算出 | 代替なし（同ロジックを再現する履歴が必要） | いいえ | **kabu APIだけでは不足** | paper_trade の `entry_price` 推定に使用中 |
| `IntradaySignals.price_5min_ago` | 1分 `closes` | 同上 | いいえ | **kabu APIだけでは不足** | 現行ロジックで必須 |
| `IntradaySignals.vol_3m_gt_prev_3m` | 1分 `vols` | 同上 | いいえ | **kabu APIだけでは不足** | （paper CSV には出していないが）通常監視系で加点に利用 |
| 気配情報（複数段） | なし（未使用） | `Sell*` / `Buy*` / Bid/Ask 系 | はい（新機能向け） | すぐ置換可能〜 | bot 未統合。**新しいエッジのみ**としての利用余地 |

### 3.4 paper_trade ループでの補助: MA25

| 既存bot項目名 | 現在の取得元 | kabu `/board` | 置換可能か | 分類 | 注意点 |
|---------------|-------------|---------------|------------|------|--------|
| `fetch_ma25` | Yahoo（日足終値からの単純SMA） | **なし**（25営業日分の終値系列が必要） | いいえ | **kabu APIだけでは不足**（`/board` のみでは不可） | 別API・別データソースでの series 蓄積が必要 |

---

## 4. 分類まとめ

| 分類 | 内容（本ドキュメント範囲） |
|------|----------------------------|
| **すぐ置換可能** | 現値・前日関連・日中OHLC・売買高・売買代金・気配オブジェクトなど、ボードが数値/object で返す項目のうち、`Quote` や将来的な気配機能に載せられるもの |
| **加工すれば置換可能** | 騰落率の「API値 vs 再算出」統一、`CurrentPriceTime` のパース、時価総額、`VWAP` のデータソース統一による乖離率の再解釈 |
| **kabu APIだけでは不足** | **1分足の配列全体**に依存する `recent_5m_high` / `price_5min_ago` / `vol_3m_gt_prev_3m`、`MA25` のような複数日履歴に基づく指標 |
| **Yahoo維持が妥当**（現構成のまま） | **日中のintraday構造解析** と **現在の VWAP算出実装の前提（1分足と同一ソース）** を壊したくない場合。`/board` の `VWAP` を追加利用する場合は、**現値+VWAP+乖離をすべて kabu に寄せる**など方針を決めてから |

## 5. VWAP と 1分足を Yahoo 維持とする理由（明示）

現行実装では `fetch_latest_intraday_data_for_paper_trade` が次を前提にしています。

1. **`fetch_intraday_1m_series`**  
   Yahoo chart から **1分足の `high` / `close` / `volume` の配列** を取得し、`calc_intraday_signals_from_series` で  
   - 直近5本の高値（最新足を除く）＝ **`recent_5m_high`**  
   - 5本前終値＝ **`price_5min_ago`**  
   - 直近3分出来高 vs その前3分＝ **`vol_3m_gt_prev_3m`**  
   を計算します。  
   **kabu の `/board` はこれらを再構成するための時系列バーを返しません**（単時点スナップショット）。

2. **`fetch_vwap`**  
   `yahoo_kabu_watch.py` の実装どおり、**同一の Yahoo 1分足 chart 応答**に含まれる `vwap` 配列を優先し、なければ **典型価格×出来高** で **累積VWAP** を推定しています。  
   つまり **VWAP スカラーは「その1分足系列」と同源**であり、`IntradaySignals.vwap_distance_pct` も **`q.price`**（現在は provider により kabu/Yahoo のいずれか）との組み合わせで解釈されます。  
   **ここだけ kabu の `VWAP` に差し替えると、「価格は kabu・VWAP は Yahoo の推定」といった二本立てになり**、ログ上の乖離率の意味がブレやすいため、Phase では **series と VWAP は Yahoo に残す**のが妥当です。

3. **将来 `/board` ベースに寄せる場合**の推奨  
   - **案A**: `CurrentPrice`・`HighPrice`・`VWAP`・`TradingVolume` をすべて kabu から取り、`vwap_distance_pct` の定義も **「kabu 公式VWAP 対 kabu 現値」** に変更する  
   - **案B**: 1分足相当を kabu の別機能（または自前キャッシュ）で構築できた時点で、intraday を丸ごと移行する  

までをマイルストンにすると安全です。

## 6. 「現値以外に kabu へ置換できそうな項目」（優先度高）

※ paper_trade と一般 `Quote` 利用に直結するもの。

| 目的 | kabu フィールド | メモ |
|------|-----------------|------|
| スクリーニング強化・整合 | `ChangePreviousClosePer` と `PreviousClose` | 算出元の単一化 |
| 日中レンジ | `OpeningPrice`, `HighPrice`, `LowPrice` + 時刻系 | Embed や理由説明に利用可 |
| 売買代金ベースの簡易指標 | `TradingValue` | bot 未定義だが追加容易 |
| 時価総額近似 | `TotalMarketValue` | Yahoo `market_cap` に相当し得る |
| 気配・板 | `Sell1…10`, `Buy1…10`, `Bid*`/`Ask*` | 新規ロジック向け |

---

*（OpenAPI は kabu.com 側の改定により変わることがあります。差分は `scripts/kabu_api_check.py` の `_compare_schema_vs_response` で都度確認してください。）*
