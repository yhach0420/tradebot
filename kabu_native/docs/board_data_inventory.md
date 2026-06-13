# Phase334 — 板情報データ在庫（Board Data Inventory）

**更新:** 2026-06-09  
**関連:** [realtime_board_exit_feasibility.md](realtime_board_exit_feasibility.md) · [estimated_runtime_cost.md](estimated_runtime_cost.md)  
**監査:** `phase300_board_live_payload_availability_report.json`（同日再実行済み）

---

## 1. 目的

small paper observer が利用しうる **板（オーダーブック）関連フィールド**を、取得経路（REST / PUSH / ログ）ごとに整理する。

---

## 2. 取得経路サマリ

| 経路 | エンドポイント | レジスト上限 | 更新方式 | 板深度 |
|------|----------------|-------------|----------|--------|
| **REST** | `GET /board/{code@exchange}` | PUSH と **共有 50 銘柄** | オンデマンド（ポーリング） | Buy1–10 / Sell1–10 あり |
| **PUSH** | `PUT /register` + WebSocket | **50 銘柄**（`KABU_PUSH_REGISTER_LIMIT`） | 板・現値の変化時プッシュ | 実測では深度あり（§3.2） |
| **push_jsonl** | 場中記録 | 登録銘柄のみ | PUSH と同期 | REST 相当の深度を含む |
| **small_paper_events** | observer イベント CSV | — | イベント駆動 | **集約値のみ**（生板なし） |

定数: `kabu_native/src/api/kabu_register.py` → `KABU_PUSH_REGISTER_LIMIT = 50`

---

## 3. フィールド一覧

### 3.1 REST `/board`（公式スキーマ）

`kabu_native/scripts/check_api.py` の `BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS`（63 キー）より、EXIT 関連で重要なもの:

| カテゴリ | フィールド |
|----------|-----------|
| 現値・OHLC | `CurrentPrice`, `CurrentPriceTime`, `OpeningPrice`, `HighPrice`, `LowPrice`, `CalcPrice` |
| 出来高・VWAP | `TradingVolume`, `TradingValue`, `VWAP` |
| 最良気配 | `BidPrice`, `BidQty`, `AskPrice`, `AskQty`, `BidTime`, `AskTime` |
| 深度 10 段 | `Buy1`–`Buy10`, `Sell1`–`Sell10`（各 `Price`, `Qty`, `Sign`, `Time`） |
| 需給補助 | `OverSellQty`, `UnderBuyQty`, `MarketOrderBuyQty`, `MarketOrderSellQty` |
| メタ | `Symbol`, `SymbolName`, `Exchange`, `SecurityType` |

### 3.2 PUSH（コード上の期待フィールド vs 実測）

**コード定義**（`EXPECTED_PUSH_FIELDS_STOCK`）はトップオブブック中心:

```
Symbol, SymbolName, Exchange, CurrentPrice, CurrentPriceTime,
TradingVolume, TradingVolumeTime, BidPrice, BidQty, AskPrice, AskQty,
VWAP, TradingValue, HighPrice, LowPrice, OpeningPrice
```

**実測**（`kabu_native/data/push_jsonl/`、2026-06-05〜09、50 銘柄/日）:

| 項目 | 結果 |
|------|------|
| `BidQty` / `AskQty` 非 null 率 | **100%**（サンプル銘柄） |
| `Buy1`–`Buy10` / `Sell1`–`Sell10` | **100%**（PUSH ペイロードに含まれる） |
| 1 銘柄あたりメッセージ数/日 | 約 150〜150,000 行（流動性依存） |
| 全 50 銘柄合計/日 | 約 **1.1M〜1.3M** 行 |

> **注意:** Phase300 は「PUSH spec に深度キーは無い」と記録しているが、**実運用の push_jsonl では深度が届いている**。`calc_board_imbalance` は深度＋最良気配の合算で計算するため、PUSH 実測パスでは REST と同等の定義が使える。

### 3.3 板インバランス定義（本番共通）

```python
# screening/morning_screen.py — calc_board_imbalance()
bid = BidQty + sum(Buy1..Buy10.Qty)
ask = AskQty + sum(Sell1..Sell10.Qty)
imbalance = bid / (bid + ask)   # 0〜1、None if total<=0
```

利用箇所:

| モジュール | タイミング | 出力フィールド |
|-----------|-----------|---------------|
| `board_imbalance_shadow.py` | ENTRY 毎 PUSH | `entry_order_book_imbalance`, `entry_imbalance_percentile` |
| `morning_screen.py` | 朝スクリーニング REST | `board_imbalance` |
| `shadow/runner.py` | REST ポール毎 | `board_imbalance` → `kabu_exit_v1` |
| `microstructure_runtime.py` | リプレイ研究 | `imbalance_collapse_streak` 等 |

---

## 4. パイプライン別の板データ到達点

### 4.1 small paper live（現行）

```
PUSH msg → pilot_runner._process_push_payload
         → LiveFeatureBridge.enrich_payload（価格・VWAP のみ追加）
         → compute_entry_order_book_imbalance_field(payload)  # 毎メッセージ
         → observer.on_tick（価格ベース EXIT のみ）
```

| 段階 | 板データの扱い |
|------|---------------|
| PUSH 受信 | 生ペイロードに `BidQty`/`AskQty`/深度あり |
| Feature bridge | **板は未使用**（`CurrentPrice`, `VWAP` のみ） |
| ENTRY pregate | `entry_order_book_imbalance` を **毎 tick 再計算**（trade dict 更新） |
| ENTRY accept | `entry_imbalance_percentile` を **凍結**（`entry_shadow`） |
| HOLD / EXIT | Phase332 **board-dynamic trailing** は **凍結 percentile のみ**使用；**リアルタイム板は未参照** |

### 4.2 shadow runner（旧系・参考）

- REST `/board` を `poll_interval_sec`（既定 15s）でポーリング
- `evaluate_kabu_signal_v1` → `board_imbalance`
- 保有中: `imbalance_low_streak` + `evaluate_kabu_exit_v1`（`board_imbalance_deterioration`）

### 4.3 ログ・成果物に残る板情報

| 成果物 | 板関連フィールド | 備考 |
|--------|-----------------|------|
| `push_jsonl/*.jsonl` | 生 PUSH 全フィールド | 再計算・リプレイの正 |
| `small_paper_events.csv` | `entry_order_book_imbalance`, `entry_imbalance_percentile`, `board_dynamic_trailing_*` | 生 `BidQty`/`AskQty` は **非永続** |
| `phase107_data_source_inventory_*.csv` | `kabu_push_jsonl` 行 | tick/board snapshot と記載 |
| Discord REJECT | なし | board フィールド未掲載（Phase300 gap） |

Phase300 archived scan（20260604–05 の 4 セッション）では、イベント CSV に `entry_order_book_imbalance` 非 null が 0 件 — **ログ列が後追い追加されたため旧セッションは空**。合成パス probe は正常（imbalance=0.48 計算可）。

---

## 5. 現時点で「取得可能」と言えるもの

| データ | live PUSH | REST | ログ再現 |
|--------|-----------|------|----------|
| 最良気配量（BidQty/AskQty） | ✅ | ✅ | ✅ push_jsonl |
| 10 段深度 | ✅（実測） | ✅ | ✅ push_jsonl |
| `calc_board_imbalance` | ✅ | ✅ | ✅ |
| スプレッド bps | 算出可 | 算出可 | 要再計算 |
| リアルタイム板変化（保有中） | **データは届くが未消費** | ポールで可能 | push_jsonl で事後再現可 |

---

## 6. ギャップ（Phase334 時点）

1. **コード spec と実測の乖離** — `EXPECTED_PUSH_FIELDS_STOCK` に深度なし；実 push_jsonl は深度あり。spec 更新またはコメント追記を推奨。
2. **イベント CSV** — 生板カラムなし；事後分析は push_jsonl 必須。
3. **保有中板監視** — データ到達は `_process_push_payload` まで来ているが、`observer.on_tick` は未使用（feasibility 文書参照）。
4. **REST live probe** — Phase300 実行時は reachability=true だが `/board` live probe は skipped（休場等）。次の場中セッションで再確認推奨。

---

## 7. 参照ファイル

| 種別 | パス |
|------|------|
| 板 imbalance 計算 | `kabu_native/src/screening/morning_screen.py` |
| ENTRY 板 shadow | `kabu_native/src/small_paper/board_imbalance_shadow.py` |
| PUSH クライアント | `kabu_native/src/api/push_client.py` |
| レジスト上限 | `kabu_native/src/api/kabu_register.py` |
| API 層ドキュメント | `kabu_native/docs/api_layer.md` |
| Phase300 レポート | `kabu_native/results/reports/phase300_board_live_payload_availability_report.json` |
