# 日本株デイトレ支援システム — 設計仕様書

**対象リポジトリ:** tradebotfile  
**想定読者:** 本システムを初めて触れる開発者・運用者  
**PDF 化:** 推奨は `python tools/md_to_pdf.py docs/DESIGN.md docs/DESIGN.pdf`（日本語フォントを PDF に埋め込み、表・コードの視認性を調整済み）。代替として VS Code / Cursor のプレビューから「印刷 → PDFへ保存」、または `pandoc docs/DESIGN.md -d tools/design-pandoc-pdf.yaml -o docs/DESIGN.pdf`（要: Pandoc + Typst）。

---

## 目次

1. [目的と位置づけ](#1-目的と位置づけ)
2. [システム構成](#2-システム構成)
3. [監視銘柄の解決順](#3-監視銘柄の解決順)
4. [機能一覧](#4-機能一覧)
5. [判断ロジックの詳細](#5-判断ロジックの詳細)
6. [売買判断指標の数値と説明](#6-売買判断指標の数値と説明)
7. [環境変数・外部連携](#7-環境変数外部連携)
8. [依存関係・実行](#8-依存関係実行)
9. [設計上の注意](#9-設計上の注意)

---

## 1. 目的と位置づけ {#1-目的と位置づけ}

本システムは **Yahoo Finance の非公式 API** から日本株の相場データを取得し、**ユーザーが定義した条件に合う銘柄を検出して通知する**ためのツール群である。

- **証券会社への発注機能はない**（スクリーニング・通知・検証用途）。
- 中核は **`yahoo_kabu_watch.py`**（監視・Replay・朝スクリーニング・1分足キャッシュ・集計が集約）。
- 補助は **`discord_issue_bot.py`**（Discord から GitHub Issue 作成、`watchlist.json` 更新など）。

**注意:** 非公式 API のため仕様変更・レート制限で動作が変わる可能性がある。1分足の取得可能期間は実質 **直近約30日** 程度とコード上も想定されている。

---

## 2. システム構成 {#2-システム構成}

| 構成要素 | 役割 |
|----------|------|
| Yahoo Finance API | クォート、1分足、VWAP、5日平均出来高、日足（MA25 用）など |
| `yahoo_kabu_watch.py` | リアルタイム監視、過去1分足 Replay、朝スクリーニング、CSV キャッシュ、サマリ出力 |
| `discord_issue_bot.py` | `discord.py` ベースの Bot（Issue 作成、`!watch` 等） |
| `watchlist.json` | 監視銘柄の「正」（存在すれば毎ループ読み直し） |
| `symbols.csv` | 銘柄一覧（朝スクリーニングでは最優先で読む） |
| `configs/*.json` | Replay 用戦略（早期撤退・後場・`entry_filters` / `regime_filters` / `signal_filters` 等） |
| `configs/regime_filter_sweep/` | `--regime-filter-sweep` / `--topix-weak-threshold-sweep` が自動生成する比較用 JSON |
| `configs/signal_filter_sweep/` | `--signal-filter-sweep` が自動生成する比較用 JSON |
| `data/intraday_1m/` | 日付・銘柄別 1分足 CSV（Replay・検証） |
| `results/` | Replay サマリ、`vwap_sweep_*`、`daily_loss_stop_sweep_*`、`regime_filter_sweep_*`、`signal_filter_sweep_*`、`topix_weak_threshold_sweep_*`、`symbol_scores_latest.json` 等 |

---

## 3. 監視銘柄の解決順 {#3-監視銘柄の解決順}

### 3.1 リアルタイム監視（`yahoo_kabu_watch.py` メインループ）

1. `--watch-file` があればそのファイル（1行1銘柄）
2. なければ `--watch`（カンマ区切り）
3. どちらもなければ **`watchlist.json` が存在すれば常にそれを優先**（壊れた JSON のときは前回リスト維持）
4. `watchlist.json` が無ければ **`symbols.csv` → スクリプト先頭の `WATCH` 定数**

### 3.2 朝スクリーニング（`--morning-screen`）

**別ルール:** `symbols.csv` があれば最優先 → なければ `watchlist.json` → なければ `WATCH`。

---

## 4. 機能一覧 {#4-機能一覧}

| 機能 | 起動の目安 | 概要 |
|------|------------|------|
| リアルタイム監視 | `python yahoo_kabu_watch.py` | 既定間隔（デフォルト 1秒）で取得・判定・（任意で）Discord 通知 |
| 朝スクリーニング | `--morning-screen` | 寄り前の候補スコアリング、上位10銘柄を出力 |
| Replay | `--replay` | 1分足を再生し判定・期待値集計・結果ファイル出力 |
| 1分足 EOD 保存 | `--save-intraday-1m-eod` | 当日 1分足を `data/intraday_1m/` に CSV 保存 |
| キャッシュレポート | `--intraday-1m-cache-report-only` | ローカル CSV のカバレッジのみ集計 |
| VWAP sweep | `--vwap-distance-sweep` | VWAP 乖離 `entry_filters` 閾値グリッドで Replay を回し比較表を保存 |
| daily_loss_stop sweep | `--daily-loss-stop-sweep` | 当日損失ストップ ON/OFF・閾値（例: 30k/50k/70k 円/100株）を比較 |
| regime filter sweep | `--regime-filter-sweep` | `regime_filters`（朝弱・上昇銘柄割合・TOPIX_WEAK）の組合せを比較 |
| TOPIX WEAK 閾値 sweep | `--topix-weak-threshold-sweep` | `TOPIX_WEAK` 判定の `topix_weak_threshold_pct`（例: −0.2〜−0.7%）を比較 |
| signal filter sweep | `--signal-filter-sweep` | `signal_filters`（ギャップ・VWAP乖離・時刻）の AB 比較 |
| Discord Issue Bot | `discord_issue_bot.py` | `!issue` / `!watch` 等（別プロセス） |

**Discord 通知の運用方針（監視側）**

- 新しく候補に入った銘柄のみ「条件一致」通知（連続スパム防止）。
- 候補から外れたら **連続 3 ループ不一致**（`EXIT_CONFIRM_COUNT`）で条件外れ通知。
- Entry / Stop / Take の水準が **% または円**で大きく変わったら再通知。

---

## 5. 判断ロジックの詳細 {#5-判断ロジックの詳細}

**重要:** **実時間監視**と **Replay 内の候補ゲート**は同一ではない。詳細は [9. 設計上の注意](#9-設計上の注意) を参照。

### 5.1 実時間監視 — 候補（`candidates`）に入る条件

すべて満たす必要がある（欠損はその項目で不合格）。

| カテゴリ | 条件（代表定数） |
|----------|------------------|
| ブラックリスト | `results/symbol_scores_latest.json` の `blacklist_symbols` に含まれない |
| 品質ブロック | 同ファイルの `quality_blocked_symbols` に含まれない |
| 前日比 | `MIN_CHANGE_PCT`（1.0%）以上 かつ `MAX_CHANGE_PCT`（8.0%）未満 |
| 高値付近 | 現在値 ≥ 当日高値 × `MIN_RATIO_TO_DAY_HIGH`（0.98） |
| 出来高 | ≥ `MIN_VOLUME`（300,000） |
| MA25 | 取得でき、現在値 > MA25 |
| 時価総額 | 取得できた場合のみ: `MIN_MARKET_CAP`〜`MAX_MARKET_CAP`（300億〜5000億円） |
| VWAP | 取得必須。乖離率 (price−vwap)/vwap×100 ≥ `VWAP_DISTANCE_PCT`（0.5%） |
| 1分足 | 直近5分高値（最新足除く）を実体で上抜け、5分前終値より上、**直近3分出来高 > その前3分**（必須） |
| Entry 接近 | Entry = 直近5分高値 × `ENTRY_BREAKOUT_BUFFER`（1.001）。現在値 ≥ Entry × `ENTRY_NEAR_RATIO`（0.996） |

**出来高急増（現在出来高 ≥ 5日平均 × `MIN_VOLUME_SPIKE_RATIO` 2.0）** は必須ではなく、満たせば内部スコア +1。

**優先銘柄:** `priority_symbols` に加え、コード上 **9984.T / 7012.T / 9412.T** が優先セットにマージされスコア +2。

### 5.2 実時間 — Entry 上抜け（🚀）と `breakout_state`

- Entry ラインを価格が上抜けた **初回** を検知すると `breakout_state` が突破済みとなり、Embed を 🚀 側に切り替え可能。
- Entry が前回突破時から **`BREAKOUT_ENTRY_RESET_PCT`（0.3%）** 以上変わると状態リセット。
- 価格が Entry を下回ると未突破に戻す。

### 5.3 実時間 — 通知に載せる Stop / Take（目安）

- Entry 基準 **−2%** / **+4%**（`STOP_LOSS_PCT_FROM_ENTRY` / `TAKE_PROFIT_PCT_FROM_ENTRY`）。約定保証ではない。

### 5.4 Replay — 候補プール（シグナル前段）

指数銘柄（監視に含まれる場合）は評価対象外とする処理がある。

- 前日比・高値付近・出来高・MA25 は実時間と同様の閾値（同一系の判定ブロック）。
- VWAP は再生中の累積 VWAP。
- **VWAP 乖離 ≥ 0.5%**、**5分高値ブレイク**、**5分前より上**。

**Replay の候補ブロックでは、実時間にある「出来高増加（3分 vs 前3分）必須」「Entry 接近率 0.996」は要求していない。** その後の **`crossed`（Entry ライン実体上抜け）** でシグナル記録に進む。

### 5.5 Replay — 地合いレジーム（`NORMAL` / `WEAK` / `CRASH`）

各ステップでウォッチ全体に対し 1 回判定する。

**主な `market_reasons` の例**

- 日経 ETF（`INDEX_NIKKEI_ETF`）が日中 VWAP より下
- TOPIX ETF（`INDEX_TOPIX_ETF`）の騰落率（前日終値から再計算）: **≤ −1.5%** で `TOPIX_CRASH`。**−1.5% より上で、かつ `topix_weak_threshold_pct` 以下**（上限 inclusive）で `TOPIX_WEAK`。閾値は `regime_filters.topix_weak_threshold_pct` で指定でき、**未指定時は `WEAK_TOPIX_CHG_PCT_MAX`（−0.5%）** と同じ値を使う。
- 上昇銘柄割合 < `MARKET_RISING_RATIO_MIN`（0.40）
- 直近30分の解決済みシグナル失敗率 > `MARKET_ENTRY_FAIL_RATE_30M_MAX`（0.60、解決数≥3）
- 高値付近銘柄割合 < `MARKET_HIGH_UPDATE_RATIO_MIN`（0.07）
- 12:30–14:00 の **後場弱** 条件（前場高値ブレイク率・VWAP 下割合・指数弱さの組合せ）

**レジーム決定（実装）**

- **`CRASH`:** TOPIX が **−1.5% 以下** かつ異常値ガード（|変動|≤20%）を満たすときのみ。
- **`WEAK`:** `CRASH` でなく `market_reasons` が空でないとき。
- **`NORMAL`:** 上記以外。

極端な breadth（上昇銘柄割合 ≤0.25 かつ高値付近 ≤0.03）は **`BREADTH_WEAK`** として理由に加わり、**単独では `CRASH` にならない**。

### 5.6 Replay — `crossed` 後の追加ゲート（抜粋）

- **後場新規禁止**（config/CLI）: JST 12:30 以降はスキップ。
- **後場厳格化:** 出来高倍率、VWAP 乖離上限、5分高値の再ブレイク倍率、5分安値の切り上がり等（`afternoon_strict`）。
- **`topix_weak_block`:** 後場かつ `TOPIX_WEAK` が含まれるときエントリー禁止（有効時）。
- **品質系:** RSI/ATR/対 TOPIX RS などは **`suggested_block_reasons`** として記録（分析用）。NORMAL と WEAK で閾値が異なる箇所あり。
- **`entry_filters`（config）:** RSI / VWAP 乖離 / ATR の「exclude_above」を有効化すると集計対象外（`excluded_from_eval`）。
- **`regime_filters`（config・Replay）:** `crossed` 後・シグナルオブジェクト生成 **直前** に評価され、条件に当たると **`exclude`（集計対象外）** になる。
  - `disable_morning_weak`: **true** のとき、JST **11:30 前** かつ（`CRASH` または TOPIX/BREADTH/日経VWAP/失敗率/`rising<` 等の明確な弱材料）なら `REGIME_FILTER_MORNING_WEAK` でスキップ。
  - `disable_rising_ratio_lt50`: **true** かつ上昇銘柄割合が **50%未満**（コード上 `rising_ratio < 0.5`）なら `REGIME_FILTER_RISING_LT50`。
  - `disable_topix_weak`: **true** かつ（`topix_chg ≤ topix_weak_threshold_pct` **または** `market_reasons` に `TOPIX_WEAK`）なら `REGIME_FILTER_TOPIX_WEAK`。
- **`signal_filters`（config・Replay）:** 同様に `crossed` 後に評価され、該当すれば集計対象外。
  - `disable_entry_after_hhmm` + `entry_after_hhmm`（既定 **10:30**）: **true** のとき、シグナル時刻がその **JST 時刻以降** なら `SIGNAL_FILTER_ENTRY_AFTER_HHMM`。
  - `disable_gap_ge_pct` + `gap_ge_threshold_pct`（既定 **3.0%**）: **true** のとき、当日始値ギャップ（前日終値比）が **閾値以上** なら `SIGNAL_FILTER_GAP_GE`。
  - `disable_vwap_distance_ge_pct` + `vwap_distance_ge_threshold_pct`（既定 **1.5%**）: **true** のとき、VWAP 乖離率 **≥ 閾値** なら `SIGNAL_FILTER_VWAP_DIST_GE`（`entry_filters` の絶対値上限とは別ルート）。
- **`--one-trade-per-symbol-per-day`:** 同一銘柄は JST 1 日 1 回まで採用。
- **`risk_controls.daily_loss_stop`:** 当日累積損益（100株換算）が **−閾値円以下** になったら、その日の新規 ENTRY/ADD を停止（シグナルは記録しつつ `exclude` 扱いになる経路あり）。

**CRASH 時:** 市場全体を即停止するのではなく、**`crash_blocked` 等のフラグと集計**で扱い、シグナルは記録される設計（分析用）。

### 5.7 Replay — ポジション解消（`ReplaySignalEval`）

- **早期撤退（有効時）:** 部分利確前でも VWAP 割れ / 直近5分安値割れで決着。
- **ストップ:** Entry の −2% を最終防衛。
- **トレーリング:** シグナル価格 +1% で部分利確相当フラグの後、VWAP 割れまたは直近5分安値割れで手仕舞い。

**ADD1/ADD2**（`--enable-add`）は別ルールの固定利幅（`exit_style="fixed"`）で検証用。

---

## 6. 売買判断指標の数値と説明 {#6-売買判断指標の数値と説明}

本章の数値は **`yahoo_kabu_watch.py` のモジュール定数**（および Replay の `configs/*.json` で上書きされる項目）に基づく。単位が `%` のものは **パーセント表示の値**（例: `1.0` = 1.0%）である。

### 6.1 日次クォート・銘柄フィルタ（実時間・Replay 共通の土台）

| 定数名 | 値 | 意味・使われ方 |
|--------|-----|----------------|
| `MIN_CHANGE_PCT` | **1.0** | 前日比がこれ **以上** でなければ候補外（強いが弱すぎない寄りの下限）。 |
| `MAX_CHANGE_PCT` | **8.0** | 前日比がこれ **以上** なら「急騰しすぎ」で除外（上限は **未満** が通過）。 |
| `MIN_RATIO_TO_DAY_HIGH` | **0.98** | 現在値が当日高値の **98%以上** なければ高値圏ではないとみなす。 |
| `MIN_VOLUME` | **300,000** | 累計出来高（株数）の下限。実時間の候補必須。 |
| `MIN_MARKET_CAP` | **30_000_000_000** | 時価総額（Yahoo の値）が取れた場合の下限（**300億円**）。 |
| `MAX_MARKET_CAP` | **500_000_000_000** | 同上限（**5000億円**）。範囲外なら候補外。 |
| MA25 | （API 取得） | **日足終値 25 本**の単純平均。実時間では **現在値 > MA25** が必須。 |

### 6.2 出来高・VWAP・1分足シグナル（エントリー「勢い」）

| 定数名 | 値 | 意味・使われ方 |
|--------|-----|----------------|
| `MIN_VOLUME_SPIKE_RATIO` | **2.0** | 現在の累計出来高 ÷ **5日平均出来高** がこれ以上なら **スコア +1**（実時間では必須条件ではない）。 |
| `VWAP_DISTANCE_PCT` | **0.5** | 乖離率 \((price - VWAP) / VWAP × 100\) がこれ **以上**（**0.5%**）でなければならない（実時間・Replay 候補の VWAP 条件）。 |
| 直近5分高値 | （1分足から算出） | **直近5本の1分足高値**の最大（**最新の1本は含めない**）。実体上抜きで「5分ブレイク」。 |
| `ENTRY_BREAKOUT_BUFFER` | **1.001** | Entry 候補 = 直近5分高値 × この倍率（**約 0.1% 上**にラインを置く）。 |
| `ENTRY_NEAR_RATIO` | **0.996** | 実時間では **現在値 ≥ Entry × 0.996** で「Entry に十分近い」ことを必須化。 |
| 出来高増加（3分 vs 前3分） | 真偽 | 直近3分の出来高合計 **>** その前の3分合計。実時間では **必須**（`vol_3m_gt_prev_3m`）。Replay の「候補プール」段階では **未要求**（シグナルは `crossed` 以降で評価）。 |
| 5分前価格 | （1分足 close） | **5本前の終値**より現在が上でなければ「上昇傾向なし」。 |

### 6.3 通知・ブレイク状態・目安の損益ライン

| 定数名 | 値 | 意味・使われ方 |
|--------|-----|----------------|
| `EXIT_CONFIRM_COUNT` | **3** | 実時間で候補から外れたと **連続3ループ** 見てから Discord「条件外れ」を送る（チョイ捨て抑制）。 |
| `BREAKOUT_ENTRY_RESET_PCT` | **0.3** | Entry が前回突破時から **0.3%以上** 変わったら `breakout_state` をリセット（新ラインでの再ブレイクを許す）。 |
| `STOP_LOSS_PCT_FROM_ENTRY` | **0.02** | 通知に載せる損切り目安 = Entry の **−2%**（約定ロジックではない）。 |
| `TAKE_PROFIT_PCT_FROM_ENTRY` | **0.04** | 利確目安 = Entry の **+4%**（リスクリワード目安）。 |
| `LEVEL_CHANGE_PCT` | **1.0** | 候補価格が前回通知から **1%以上** 変わったら再通知。 |
| `LEVEL_CHANGE_YEN` | **10** | または **10円以上** 変化で再通知（% と円の **どちらか** で発火）。 |

### 6.4 Replay：地合い・マーケットブレッド（ウォッチ集合ベース）

| 定数名 | 値 | 意味・使われ方 |
|--------|-----|----------------|
| `INDEX_NIKKEI_ETF` | **1321.T** | 日経225連動 ETF（代用指数）。**価格 < 日中VWAP** 等で地合い理由に加わる。 |
| `INDEX_TOPIX_ETF` | **1306.T** | TOPIX 連動 ETF。前日終値から再計算した騰落率で CRASH/WEAK 理由を付与。 |
| `CRASH_TOPIX_CHG_PCT_MAX` | **−1.5** | TOPIX が **−1.5% 以下** のとき **`CRASH`**（ただし下記の異常値ガードあり）。 |
| `WEAK_TOPIX_CHG_PCT_MAX` | **−0.5** | **`regime_filters.topix_weak_threshold_pct` 未指定時**の `TOPIX_WEAK` 帯の上限（%）。指定時はその値が上限になる（`--topix-weak-threshold-sweep` で探索）。 |
| TOPIX 異常値ガード | **±20%** 以内 | 欠損や単位崩れで ±20% を超える変化は CRASH/WEAK の TOPIX 判定に **使わない**。 |
| `MARKET_RISING_RATIO_MIN` | **0.40** | 前日比プラスの銘柄割合が **40%未満** なら地合い弱理由。 |
| `MARKET_ENTRY_FAIL_RATE_30M_MAX` | **0.60** | 直近30分に **解決済み** の Replay シグナルが **3件以上** あり、かつ **LOSE 比率 > 60%** なら弱理由。 |
| `MARKET_HIGH_UPDATE_RATIO_MIN` | **0.07** | 当日高値の **99.9%以上** にいる銘柄の割合が **7%未満** なら「高値更新が薄い」弱理由。 |
| `CRASH_RISING_RATIO_MAX` | **0.25** | 上昇銘柄割合 **≤25%** かつ下記と同時なら **`BREADTH_WEAK`**（単独では `CRASH` レジームにはしない）。 |
| `CRASH_HIGH_RATIO_MAX` | **0.03** | 高値付近割合 **≤3%** と組み合わせで `BREADTH_WEAK`。 |
| `AFTERNOON_FILTER_START_MIN` / `END` | **12:30〜14:00**（分換算） | この時間帯だけ **後場弱** の追加判定（前場高値ブレイク率・VWAP 下割合など）。 |
| `AFTERNOON_BREAK_MORNING_HIGH_RATIO_MIN` | **0.10** | 各銘柄の **前場中に記録した前場高値** を、現在値が **上回っている**（`price > morning_high × 1.000`）銘柄の割合が **10%未満** で、かつ VWAP 下が多い／指数弱いときに **後場弱** 理由へ（他定数と組合せ）。 |
| `MARKET_VWAP_BELOW_RATIO_MAX` | **0.60** | VWAP より下の銘柄が **60%超** などの条件の一部（後場弱フィルタ）。 |

**レジーム集約:** **`CRASH`** は **TOPIX ≤ −1.5%**（異常値ガードあり）のとき。**`WEAK`** はそれ以外で `market_reasons` が空でないとき。`TOPIX_WEAK` の帯の上限は **`regime_filters.topix_weak_threshold_pct`** があればそれ、なければ **`WEAK_TOPIX_CHG_PCT_MAX`（−0.5%）**。

### 6.5 Replay：シグナル品質（RSI / ATR% / 対TOPIX）

| 項目 | 値 | 意味・使われ方 |
|------|-----|----------------|
| RSI 期間 | **14** | `_calc_rsi14`（終値列から Wilder 型 RSI）。 |
| ATR 期間 | **14** | `_calc_atr14`（高安終値から ATR）。`atr_pct` = ATR ÷ 価格 × 100。 |
| `SIGNAL_FILTER_RSI_BLOCK_GT` | **82.0** | **NORMAL** 相当の品質指摘で「RSI > 82」など（`suggested_block_reasons` 用。実装は分析・付与中心）。 |
| `SIGNAL_FILTER_ATR_PCT_BLOCK_GT` | **4.0** | ATR% > **4%** を危険側として指摘。 |
| `SIGNAL_FILTER_RS_BLOCK_LT` | **0.0** | 銘柄の前日比% − TOPIX%（相対強度）が **0 未満** を指摘。 |
| `WEAK_SIGNAL_FILTER_RSI_BLOCK_GT` | **78.0** | **WEAK** レジーム時は RSI 閾値を厳しく。 |
| `WEAK_SIGNAL_FILTER_ATR_PCT_BLOCK_GT` | **3.5** | **WEAK** 時は ATR% 閾値を **3.5%** に。 |
| WEAK 時の時間 | **11:30 以降** | `_quality_rejects` で **後場寄り** を `weak_not_morning` として指摘。 |

補助定数 **`WEAK_ENTRY_VWAP_DIST_PCT_MAX`（1.5%）**、**`WEAK_VOLUME_SPIKE_RATIO_MIN`（1.5倍）**、**`WEAK_REBREAK_MULT`（1.002）** はファイル上「WEAK 時の追加厳格化」用として定義されているが、現行の `_quality_rejects` 主経路では **別セットの条件**が中心（[9. 設計上の注意](#9-設計上の注意) の項番4も参照）。

### 6.6 後場エントリー厳格化（既定値・`afternoon_strict` で上書き可）

| 定数名 | 既定値 | 意味 |
|--------|--------|------|
| `AFTERNOON_ENTRY_STRICT_VOLUME_SPIKE_RATIO_MIN` | **2.0** | 累計出来高 / 5日平均 が **2.0倍以上**（後場 `strict` 時）。 |
| `AFTERNOON_ENTRY_STRICT_VWAP_DIST_PCT_MAX` | **1.0** | VWAP 乖離率が **1.0%超** なら高値掴み寄りで除外（後場）。 |
| `AFTERNOON_ENTRY_STRICT_REBREAK_MULT` | **1.0015** | 価格 ≥ 直近5分高値 × **1.0015** の「強い上抜け」を要求。 |

プリセット `replay_aggressive.json` 等では **より緩い** `volume_spike_ratio_min` / `vwap_dist_pct_max` / `rebreak_mult` が JSON 側に入る。

### 6.7 Replay：ポジション解消・ADD・その他

| 項目 | 値 | 意味 |
|------|-----|------|
| 部分利確（トレーリング） | **+1.0%** | `signal_price × 1.01` 到達で部分利確フラグ（その後 VWAP 割れ / 直近5分安値割れで手仕舞い）。 |
| ADD1 利確幅 | **+2.5%** | `tp_pct = 0.025`（固定決済スタイル）。 |
| ADD2 利確幅 | **+1.5%** | `tp_pct = 0.015`。 |
| ADD の VWAP 乖離禁止 | **3.0%超** | 乖離率 **> 3%** なら ADD 不可（コード内リテラル）。 |
| ADD の直近5分上昇禁止 | **2.0%超** | 5分上昇率 **> 2%** なら ADD 不可。 |
| ADD 最大回数 | **2** / 銘柄・日 | 同一日内。 |
| ADD 間隔 | **5分** | 前回 ADD からの経過。 |
| ADD 禁止時刻 | **14:30 以降** | JST で ADD ロジックを止める。 |
| `CROSSED_FALSE_STREAK_TO_COUNT` | **20** | Replay の集計用：`crossed` が偽が続く件数のカウント閾値。 |
| `daily_loss_stop`（既定） | **OFF**、閾値例 **50,000 円/100株** | `risk_controls.daily_loss_stop` で有効化。当日累積損が **−閾値** 以下で新規停止。 |

### 6.8 朝スクリーニング（`--morning-screen`）

| 項目 | 値 | 意味 |
|------|-----|------|
| 最低出来高（除外） | **100,000** 未満除外 | 監視本番の 30 万株より緩い（候補の幅を広げる）。 |
| 前日比 | **マイナス除外** | プラスの銘柄だけスコア対象。 |
| 前日比 +1〜5% | **+2 点** | スコアリング。 |
| 前日比 +5〜8% | **+1 点** | |
| 前日比 +8%以上 | **−2 点** | 急騰注意。 |
| 出来高 30 万+ | **+1 点** | |
| 出来高 ≥ 5日平均×**1.5** | **+2 点** | |
| 価格 > VWAP | **+2 点** | |
| 価格 > MA25 | **+1 点** | |
| 価格 ≥ 当日高値×**0.98** | **+2 点** | |
| 当日値幅（前日終値比）≥ **1%** | **+1 点** | |

### 6.9 `entry_filters`（config）の典型しきい値

`_default_replay_configs_dicts` および `_apply_replay_config_to_flags` のデフォルト例（**enabled が false の項目は無効**が基本）。

| キー | `exclude_above` 既定 | 有効時の意味（概要） |
|------|----------------------|----------------------|
| `rsi` | **75.0** | RSI がこの値 **超** なら集計対象外（`ENTRY_FILTER_RSI`）。 |
| `vwap_distance_pct` | **2.0** | 乖離率の **絶対値** が **2%超** なら除外（実装は `abs(vwap_dist)` と比較）。 |
| `atr_pct` | **4.0** | ATR% が **4%超** なら除外。 |

### 6.10 キャッシュ TTL（判定精度と負荷のトレードオフ）

| 定数名 | 秒 | 用途 |
|--------|-----|------|
| `VOL_AVG5_CACHE_TTL_SEC` | **600** | 5日平均出来高。 |
| `VWAP_CACHE_TTL_SEC` | **300** | 日中 VWAP。 |
| `INTRADAY_SERIES_CACHE_TTL_SEC` | **20** | 1分足系列（高値・終値・出来高）。 |
| `MA25_CACHE_TTL_SEC` | **600** | 25日移動平均。 |

### 6.11 Replay config — `regime_filters`（任意）

`_apply_replay_config_to_flags` が読む JSON キー（すべて **省略可**・既定はフィルタ無効）。

| キー | 型・既定 | 意味 |
|------|----------|------|
| `disable_morning_weak` | bool, **false** | **true** のとき前場の「明確な弱材料」局面でシグナルを集計対象外に（上記 §5.6）。 |
| `disable_rising_ratio_lt50` | bool, **false** | **true** かつ上昇銘柄割合 **50%未満** でスキップ。 |
| `disable_topix_weak` | bool, **false** | **true** かつ TOPIX 弱い（閾値以下または `TOPIX_WEAK` 理由）でスキップ。 |
| `topix_weak_threshold_pct` | number または省略 | **`TOPIX_WEAK` の上限（%）** および `disable_topix_weak` 判定に使用。省略時は **`WEAK_TOPIX_CHG_PCT_MAX`（−0.5）** と同じ。 |

### 6.12 Replay config — `signal_filters`（任意）

| キー | 型・既定 | 意味 |
|------|----------|------|
| `disable_gap_ge_pct` | bool, **false** | **true** のとき、始値ギャップ（前日終値比）が **`gap_ge_threshold_pct`（既定 3.0%）以上** ならシグナルを集計対象外。 |
| `gap_ge_threshold_pct` | number, **3.0** | ギャップ％のしきい値。 |
| `disable_vwap_distance_ge_pct` | bool, **false** | **true** のとき、VWAP 乖離率 **≥ `vwap_distance_ge_threshold_pct`（既定 1.5%）** なら集計対象外（上方向の「追い過ぎ」抑制）。 |
| `vwap_distance_ge_threshold_pct` | number, **1.5** | 上記のしきい値（%）。 |
| `disable_entry_after_hhmm` | bool, **false** | **true** のとき、シグナル時刻が **`entry_after_hhmm` 以降（JST）** なら集計対象外。 |
| `entry_after_hhmm` | string, **"10:30"** | 例: `"10:30"`。 |

**命名の注意:** キー名は `disable_*` だが、値が **true** のとき **フィルタが掛かり** 該当シグナルは **`excluded_from_eval`** になる（「disable = その特徴を無効化するのではなく、その条件のエントリーを止める」意味合いに近い）。

---

## 7. 環境変数・外部連携 {#7-環境変数外部連携}

### `yahoo_kabu_watch.py`

- `DISCORD_WEBHOOK_URL` — Webhook 通知
- `ALERT_CHANNEL_ID` + `DISCORD_TOKEN`（旧 `DISCORD_BOT_TOKEN` 互換）— Bot 投稿

### `discord_issue_bot.py`

- `DISCORD_TOKEN`, `GITHUB_TOKEN`, `DISCORD_WEBHOOK_URL`（必須）
- `CONTROL_CHANNEL_ID`, `ALERT_CHANNEL_ID`（任意）

---

## 8. 依存関係・実行 {#8-依存関係実行}

- Python **3.10+** 想定、`requests` 必須。Issue Bot は `discord.py` 等（`requirements.txt` に従う）。

```text
python yahoo_kabu_watch.py
python yahoo_kabu_watch.py --morning-screen
python yahoo_kabu_watch.py --replay --replay-config configs/replay_safe.json
python yahoo_kabu_watch.py --intraday-1m-cache-report-only
python yahoo_kabu_watch.py --vwap-distance-sweep
python yahoo_kabu_watch.py --daily-loss-stop-sweep
python yahoo_kabu_watch.py --regime-filter-sweep
python yahoo_kabu_watch.py --topix-weak-threshold-sweep
python yahoo_kabu_watch.py --signal-filter-sweep
```

一括比較系は内部で **`random_apr`×N** と **`random_60d`×N**（N は多くの sweep で **10**、`--regime-filter-sweep` / `--signal-filter-sweep` は **`--replay-repeat` 未指定時のみ 10**、指定時はその整数）を連続実行し、`results/<sweep名>_<時刻>/sweep_summary.txt` にまとめる。再現性が必要なら **`--replay-seed`** を併用する。

---

## 9. 設計上の注意 {#9-設計上の注意}

1. **実時間の候補条件と Replay の候補条件は完全一致しない**（Replay は「出来高増加必須」「Entry 接近率」まで要求しないブロックがある）。検証結果と実アラートの差に注意すること。
2. **地合い NORMAL/WEAK/CRASH は Replay 中心**であり、実時間メインループには組み込まれていない。
3. **非公式 API** 依存のため、欠損フィールド・レート制限への耐性が運用上重要。
4. ファイル先頭の **`WEAK_ENTRY_*` 等**は意図としての定数が残る一方、現行の `_quality_rejects` 経路では **WEAK 時は RSI/ATR/前場限定**などが中心である。
5. **`regime_filters` / `signal_filters`** のキー名 `disable_*` は直感と逆に感じることがある。**true = その条件でシグナルを除外（集計対象外）** という意味で読む（詳細は **§6.11・§6.12**）。

---

## TODO / ロードマップ

TODO は設計仕様書とは別ファイルで管理する。

- `docs/TODO.md`

## 改訂履歴

| 日付 | 内容 |
|------|------|
| 2026-05-09 | 初版（コードベースに基づく設計仕様の文書化） |
| 2026-05-09 | 今後のTODO（参考）を追記 |
| 2026-05-09 | TODOランキングと戦略評価メモを追記 |
| 2026-05-09 | §6「売買判断指標の数値と説明」を追加（定数・朝スクリ・Replay 専用指標）。章番号繰り下げ（環境変数=§7〜）。 |
| 2026-05-09 | 現行コードに合わせ更新：`regime_filters` / `signal_filters`、`TOPIX_WEAK` 可変閾値、各種 sweep CLI・出力パス、§5.6 の ENTRY 前ゲート説明。 |
