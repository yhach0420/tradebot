# kabu 前提シグナル再設計（Phase 5B）

## 背景と方針

Phase 5A により、kabu PUSH 由来の近似 1 分足は **Yahoo 1 分足のドロップイン置換不可**であることが確認された（低出来高 tier で比較可能分 ~25%、close 平均絶対差 ~11 円、breakout / entry 判定の大量不一致）。

**方針**

- 既存 `signal_engine`（Yahoo プロファイル）を **そのまま kabu に流用しない**。
- kabu REST / PUSH で **安定して取れるフィールド**を一次データとし、**`kabu_signal_v1`** として別プロファイルを定義する。
- Yahoo 版は `yahoo_signal_v1`（現行 `signal_engine` + 監視ループ）として維持し、設定で切り替える。

```
┌─────────────────┐     ┌──────────────────┐
│ kabu PUSH/REST  │────▶│ KabuBoardSnapshot │
└─────────────────┘     └────────┬─────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
           ┌────────────────┐          ┌────────────────┐
           │ kabu_signal_v1 │          │ yahoo_signal   │
           │  (新・本設計)   │          │ (既存・維持)   │
           └────────┬───────┘          └────────┬───────┘
                    │                             │
                    └──────────────┬──────────────┘
                                   ▼
                          paper_trade / Discord
                          （profile で分岐）
```

---

## 1. kabu 用シグナルの入力データ

### 1.1 一次入力（PUSH 優先・REST 補完）

各評価サイクル（推奨: **PUSH 受信時** + 最大 **15 秒に 1 回**の REST 板フォールバック）で、銘柄 1 件分のスナップショット `KabuBoardSnapshot` を組み立てる。

| フィールド | ソース | 安定性 | 用途 |
|------------|--------|--------|------|
| `CurrentPrice` | PUSH / REST | ◎ | 現値・ブレイク判定 |
| `CurrentPriceTime` | PUSH / REST | ◎ | **鮮度**・セッション内時刻 |
| `VWAP` | PUSH / REST | ◎ | 乖離率（**API セッション VWAP を正**） |
| `TradingVolume` | PUSH / REST | ◎ | 累積出来高 → **差分**で区間出来高 |
| `TradingValue` | PUSH / REST | ◎ | 売買代金フィルタ・流動性 |
| `HighPrice` | PUSH / REST | ◎ | **セッション高値**接近・ブレイク |
| `LowPrice` | PUSH / REST | ◎ | レンジ・リスク参考 |
| `OpeningPrice` | PUSH / REST | ○ | 寄り付きからの位置 |
| `BidPrice` / `AskPrice` | PUSH / REST | ◎ | スプレッド |
| `BidQty` / `AskQty` | PUSH / REST | ◎ | 最良気配厚み |
| `Sell1`〜`Sell5` | PUSH / REST | ○ | 売り板厚み・壁 |
| `Buy1`〜`Buy5` | PUSH / REST | ○ | 買い板厚み・壁 |
| `PreviousClose` | REST（初回） | ○ | 前日比（任意ゲート） |

**PUSH に無いとき**: 当該銘柄のみ `GET /board/{symbol@exchange}` で補完。昼休み・大引け後は PUSH 停止のため **評価自体を停止**（既存仕様と同様）。

### 1.2 二次入力（アプリ側で派生）

| 派生系列 | 生成方法 | 備考 |
|----------|----------|------|
| `push_samples_1m` | 直近 60 秒の PUSH 件数 | 疎密・品質ゲート |
| `volume_delta_30s` | `TradingVolume` の 30 秒差分 | 出来高増の主指標 |
| `volume_delta_3m` | 3 分窓の累積差分 | 緩い出来高確認 |
| `price_ring_5m` | 直近 5 分の `CurrentPrice` リング（最大 300 サンプル） | **ローリング高値**（Yahoo `recent_5m_high` の代替） |
| `approx_bar_1m` | `MinuteBarBuilderFromPush` の確定バー | 補助のみ・**判定の主軸にしない** |
| `quote_age_sec` | `now_utc - CurrentPriceTime` | 鮮度 |
| `spread_yen` / `spread_bps` | `AskPrice - BidPrice` | 板品質 |
| `book_imbalance` | 買い板量 / 売り板量（下記式） | 需給 |

**板バランス（kabu_signal_v1 案）**

```text
bid_depth  = BidQty + Σ Buy1..Buy5.Qty
ask_depth  = AskQty + Σ Sell1..Sell5.Qty
book_imbalance = bid_depth / (bid_depth + ask_depth)   # 0.5 が中立
```

### 1.3 入力として使わない／弱いもの

| 項目 | 理由 |
|------|------|
| Yahoo 1 分足 CSV | kabu プロファイルでは参照しない |
| 1 分足「陽線」単体 | PUSH 疎で OHLC が欠落・歪む（Phase 5A） |
| `recent_5m_high`（1 分足 max[-6:-1]） | そのままでは再現性が低い → **ローリング高値**に置換 |
| MA25 / 5 日平均出来高（Yahoo chart） | v1 では **kabu セッション累積**で代替。必要なら REST 日足を別フェーズで追加 |

---

## 2. Yahoo 由来シグナルから残すもの（概念）

| 概念 | Yahoo 実装 | kabu_signal_v1 での対応 |
|------|------------|-------------------------|
| **VWAP 上** | `(price-vwap)/vwap*100 >= 0.5%` | 同概念。**`VWAP` フィールドを直接使用**（バー再計算しない） |
| **高値接近** | `price > recent_5m_high` + entry 近接 | **`price >= HighPrice * near_ratio`** または **`price >= rolling_high_5m * buffer`** |
| **出来高増** | 直近 3 分 vs 前 3 分（1 分足） | **`volume_delta_30s` / 直近 3 分累積差分** が閾値超 |
| **breakout** | `price >= entry` の初回（状態機械） | **`price >= trigger_level` の初回**（trigger は下記） |

---

## 3. Yahoo 由来シグナルから変更するもの

| 項目 | Yahoo（現行） | kabu_signal_v1（変更） | 変更理由 |
|------|---------------|------------------------|----------|
| **recent_5m_high** | 1 分足 high の max（最新足除外） | **`rolling_high_5m`** = 直近 5 分の `CurrentPrice` 最大（現在値は含めない） | PUSH サンプルベースで欠落に強い |
| **entry 候補** | `recent_5m_high * 1.001` | **`trigger_level = max(rolling_high_5m, HighPrice) * 1.0005`**（初期値・要チューニング） | セッション高値と短期抵抗の合成 |
| **entry 近接** | `price >= entry * 0.996` | `price >= trigger_level * 0.998` | 板更新のジッタを吸収 |
| **上昇傾向** | `price > price_5min_ago`（close） | **`price > price_3m_ago`**（リングから 3 分前サンプル、無ければスキップ） | 1 分足 close に依存しない |
| **1 分足陽線** | （監視系で暗黙に使う箇所あり） | **v1 では採用しない** | 近似足の open/close は信頼しない |
| **breakout_cross_now** | 分足終端 close 基準の状態機械 | **PUSH ごと**に評価。`price >= trigger` かつ `breakout_state=False` → 発火 | タイミングを「更新イベント」に合わせる |
| **signal_score** | 全ゲート通過で実質 0/1 | **0〜100 の加重スコア**（下記）。通知は **スコア + 必須ゲート** | 疎な PUSH でも段階的にランク付け |
| **VWAP 閾値** | 0.5% 固定 | **0.35% 初期値**（kabu VWAP は公式値のため Yahoo より信頼） | Phase 5A で乖離定義が異なるため再較正 |

**breakout 状態リセット（Yahoo から継承・緩和）**

- 前回突破時の `trigger_level` から **0.5%** 以上変化 → `breakout_state = false`（Yahoo は 0.3%）。

---

## 4. kabu 用に追加する候補（v1 で採用するもの）

| 指標 | 定義 | ゲート / スコア | 初期閾値（案） |
|------|------|-----------------|----------------|
| **板厚み** | `bid_depth + ask_depth` | スコア加点 | 上位ウォッチ銘柄の当日 p20 未満は減点 |
| **買い板優勢** | `book_imbalance` | 必須ではない・スコア | `>= 0.52` で +10 |
| **spread** | `(Ask-Bid)/mid * 10000` bps | **必須ゲート** | `<= 15` bps（株価帯で段階化は v1.1） |
| **鮮度** | `quote_age_sec` | **必須ゲート** | `<= 20` 秒（REST フォールバック時は `<= 45`） |
| **HighPrice 接近率** | `CurrentPrice / HighPrice` | 必須ゲート | `>= 0.985`（セッション高の 98.5% 以上） |
| **VWAP 乖離率** | `(price - VWAP) / VWAP * 100` | 必須ゲート | `>= 0.35` % |
| **約定更新頻度** | `push_samples_1m` | **銘柄フィルタ** | `>= 8` 件/分（下記） |
| **区間出来高** | `volume_delta_30s` | 必須ゲート | 銘柄別: `max(5000, 0.001 * TradingValue当日)` など |

**採用しない（v1 見送り）**

- 10 段全板の機械学習特徴量
- ティック方向推定（約定履歴 API 未使用）

---

## 5. 銘柄フィルタ（低出来高・PUSH 疎の扱い）

Phase 5A: 低出来高 tier は **比較可能 1 分が ~25%**、高 tier は ~75%。  
→ **kabu_signal_v1 の対象銘柄を「流動性 + PUSH 密度」で事前に絞る。**

### 5.1 ウォッチリスト登録前（日次・静的条件）

| 条件 | 初期値 | 不合格時 |
|------|--------|----------|
| 当日累積 `TradingValue` | `>= 500_000_000` 円（5 億） | `UNIVERSE_REJECT_VALUE` |
| 当日累積 `TradingVolume` | `>= 300_000` 株（Yahoo `MIN_VOLUME` と同水準） | `UNIVERSE_REJECT_VOLUME` |
| 時価総額 | 既存 `MIN_MARKET_CAP`〜`MAX_MARKET_CAP` を継承（REST/朝スクリーニング） | 既存と同様 |

### 5.2 セッション中（動的・PUSH 品質）

直近 **10 分**のローリングで評価（1 分ごと更新）。

| 条件 | 初期値 | 不合格時 |
|------|--------|----------|
| `push_samples_1m` の平均 | `>= 8` | `QUALITY_REJECT_SPARSE_PUSH` → **当セッションはシグナル停止** |
| `quote_age_sec` の p95 | `<= 30` | `QUALITY_REJECT_STALE` |
| `spread_bps` の中央値 | `<= 20` | `QUALITY_REJECT_WIDE_SPREAD` |
| 近似 1 分足の欠落率 | 10 分で **6 分以上バー無し** | `QUALITY_REJECT_BAR_GAP` |

**低出来高銘柄の扱い（決定）**

- **原則**: 上記動的ゲートに **1 つでも該当した銘柄は kabu プロファイルのエントリー対象外**（ウォッチ表示のみ、または Yahoo プロファイルにフォールバックは **v1 では行わない**）。
- **理由**: フォールバック混在は paper_trade の検証を複雑化するため。
- **例外**: 手動 `force_kabu_signal` フラグ（開発用）のみ。

### 5.3 銘柄 tier（運用ラベル）

| Tier | 目安（当日出来高順） | kabu_signal_v1 |
|------|----------------------|----------------|
| A | 上位 1/3 | 通常閾値 |
| B | 中位 1/3 | `volume_delta_30s` を 1.2 倍に |
| C | 下位 1/3 かつフィルタ通過 | 通知のみ（エントリー候補は Discord 参考）※paper_trade 自動エントリーは **v1 非対応** |

※ Tier C は Phase 5A でズレが大きい帯域のため、**自動売買には載せない**のが v1 の決定事項。

---

## 6. `kabu_signal_v1` 仕様

### 6.1 プロファイル識別

```yaml
signal_profile: kabu_signal_v1   # または yahoo_signal_v1
market_data:
  quote: kabu_push               # CurrentPrice, VWAP, ...
  board_depth: 5                 # Sell1-5 / Buy1-5
  aux_bars: push_approx_1m       # 補助・品質監視のみ
```

### 6.2 必須ゲート（すべて満たす → `timing_ok = true`）

記号: `P` = CurrentPrice, `H` = HighPrice, `V` = VWAP, など。

```text
G1  鮮度:     quote_age_sec <= 20
G2  スプレッド: spread_bps <= 15
G3  VWAP上:   (P - V) / V * 100 >= 0.35
G4  高値接近:  P / H >= 0.985
G5  短期高値:  P > rolling_high_5m        # 厳密には「上抜け」
G6  出来高:    volume_delta_30s >= vol_threshold(symbol)
G7  流動性:    TradingValue_today >= 5e8  (REST キャッシュ可)
G8  PUSH密度:  push_samples_1m >= 8
```

不合格理由は `reject_codes: list[str]` として列挙（Yahoo の日本語理由文字列とは別コード体系）。

### 6.3 トリガー価格（entry 相当）

```text
rolling_high_5m = max(CurrentPrice samples in (now-5m, now) ), 現在サンプル除く
trigger_level   = max(rolling_high_5m, HighPrice) * (1 + TRIGGER_BUFFER)
TRIGGER_BUFFER  = 0.0005   # 0.05%、Yahoo 1.001 より控えめ
near_ok         = P >= trigger_level * 0.998
breakout_event  = P >= trigger_level AND NOT breakout_state
```

`breakout_state` / リセットは Yahoo 版 `BreakoutStateTracker` と同型だが、**閾値・trigger の定義だけ kabu 用**。

### 6.4 signal_score（0〜100）

必須ゲート未達なら **0**。達した場合のみ加点:

| 成分 | 条件 | 点数 |
|------|------|------|
| 基礎 | `timing_ok` | 40 |
| VWAP 乖離 | `vwap_dist_pct >= 0.8` | +15 |
| 出来高 | `volume_delta_30s` が直近 30 分 p75 超 | +15 |
| 板優勢 | `book_imbalance >= 0.55` | +10 |
| 高値更新 | `P >= H * 0.995` | +10 |
| 更新頻度 | `push_samples_1m >= 15` | +10 |

**通知条件（Discord / 🚀）**

```text
notify_breakout =
    breakout_event
    AND timing_ok
    AND signal_score >= 60
    AND tier in (A, B)
```

**参考通知（エントリー近接のみ）**

```text
notify_near =
    near_ok AND NOT breakout_state AND timing_ok AND signal_score >= 50
```

### 6.5 出力データ構造（実装前の契約）

```json
{
  "profile": "kabu_signal_v1",
  "symbol": "9984",
  "exchange": 1,
  "evaluated_at_utc": "2026-05-16T03:15:00Z",
  "current_price": 5745.0,
  "current_price_time": "2026-05-15T15:30:00+09:00",
  "quote_age_sec": 2.1,
  "vwap": 5861.69,
  "vwap_distance_pct": -1.99,
  "high_price": 6020.0,
  "high_proximity_ratio": 0.954,
  "rolling_high_5m": 5750.0,
  "trigger_level": 5752.88,
  "volume_delta_30s": 12000,
  "push_samples_1m": 14,
  "spread_bps": 6.9,
  "book_imbalance": 0.61,
  "timing_ok": false,
  "reject_codes": ["G4_HIGH_PROXIMITY", "G3_VWAP_DIST"],
  "signal_score": 0,
  "breakout_event": false,
  "breakout_state": false,
  "tier": "A"
}
```

---

## 7. Yahoo 版との差分一覧

| 観点 | Yahoo (`yahoo_signal_v1` / `signal_engine`) | kabu (`kabu_signal_v1`) |
|------|-----------------------------------------------|-------------------------|
| 主データ | Yahoo chart 1m + quote API | kabu PUSH + board REST |
| VWAP | chart 由来 / キャッシュ | **板 `VWAP` フィールド** |
| 抵抗線 | `recent_5m_high`（1 分足） | `rolling_high_5m` + `HighPrice` |
| 出来高増 | 3 分 vs 3 分（1 分足） | `TradingVolume` 差分（30s / 3m） |
| 陽線・分足形状 | 暗黙依存あり | **不使用** |
| breakout タイミング | ループ毎（~1s）+ 分足 close | **PUSH イベント** |
| 板・スプレッド | なし | **必須ゲート** |
| 鮮度 | 1 分足 stale チェック | **`CurrentPriceTime` 秒** |
| スコア | 0/1 相当 | **0〜100** |
| 低流動銘柄 | `MIN_VOLUME` のみ | **PUSH 密度 + バー欠落で除外** |
| 昼休み / 引け後 | Yahoo は取得可 | **PUSH 停止 → 評価停止** |

---

## 8. paper_trade への接続方針

### 8.1 段階的接続（推奨）

| 段階 | 内容 | リスク |
|------|------|--------|
| **B1** | `kabu_signal_v1` を **ログのみ**（`results/kabu_signal/`）で PUSH ループに載せる | 低 |
| **B2** | paper_trade で `signal_profile=kabu_signal_v1` 時、**仮想エントリー**を kabu trigger に切替 | 中 |
| **B3** | Yahoo プロファイルと **並走シャドウ**（同一銘柄で両方記録、PnL は kabu のみ） | 中 |
| **B4** | 検証後、ウォッチのデフォルトを kabu に | 要合意 |

### 8.2 コード上の分割（実装時）

```
src/
  signal_engine.py          # yahoo_signal_v1（現状維持）
  kabu_signal_engine.py     # kabu_signal_v1（新規・本設計の実装先）
  kabu_board_snapshot.py    # PUSH/REST → KabuBoardSnapshot
```

- `yahoo_kabu_paper_trade_impl.py` には **プロファイル分岐ファサード**のみ追加（`evaluate_entry_signal(profile, snapshot)`）。
- 既存 CSV 列は互換のため **`trigger_level` を `entry_price` 列にマップ**（意味はコメントで明示）。

### 8.3 paper_trade で変えないもの（v1）

- 損切り・利確・トレーリング・地合いフィルタ（TOPIX 代用等）は **既存エンジン**を継続。
- 変えるのは **「エントリー候補の生成・ブレイク検知」** の入力と閾値のみ。

### 8.4 設定例（将来 `.env` / runtime JSON）

```env
SIGNAL_PROFILE=kabu_signal_v1
MARKET_DATA_PROVIDER=kabu
KABU_SIGNAL_MIN_PUSH_PER_MIN=8
KABU_SIGNAL_VWAP_DIST_MIN_PCT=0.35
KABU_SIGNAL_SPREAD_BPS_MAX=15
KABU_UNIVERSE_MIN_TRADING_VALUE=500000000
```

---

## 9. 実装前チェックリスト

| # | 項目 | 状態 |
|---|------|------|
| 1 | 一次入力フィールド一覧 | 本文 §1 |
| 2 | Yahoo から残す概念 | §2 |
| 3 | 変更・廃止項目 | §3 |
| 4 | kabu 追加指標 | §4 |
| 5 | 低出来高・疎 PUSH の除外ルール | §5（**Tier C は自動エントリー外**） |
| 6 | 判定式・通知条件 | §6 |
| 7 | paper_trade 接続方針 | §8 |

**未確定（実 PUSH JSONL 取得後にチューニング）**

- `TRIGGER_BUFFER` / `near_ratio` の最適値
- `spread_bps` の株価帯別テーブル
- Tier A/B/C の出来高閾値（銘柄時価総額連動）

---

## 10. 関連ドキュメント

- [kabu_bar_quality.md](kabu_bar_quality.md) — Phase 5A 定量結果
- [kabu_yahoo_removal_feasibility.md](kabu_yahoo_removal_feasibility.md) — PUSH 仕様
- [signals_eval_validation.md](signals_eval_validation.md) — Yahoo プロファイル単体検証

---

## 完了条件への対応

| 完了条件 | 対応 |
|----------|------|
| Yahoo 互換ではなく kabu 前提の仕様 | **`kabu_signal_v1`** として §6 で定義 |
| 実装前に必要データ・判定が明確 | §1 入力、§6 ゲート式、§6.5 JSON 契約 |
| 低出来高銘柄の扱い | §5：**動的品質ゲート + Tier C は自動エントリー外** |
