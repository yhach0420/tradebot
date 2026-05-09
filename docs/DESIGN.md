# 日本株デイトレ支援システム — 設計仕様書

**対象リポジトリ:** tradebotfile  
**想定読者:** 本システムを初めて触れる開発者・運用者  
**PDF 化:** VS Code / Cursor のプレビューから「印刷 → PDFへ保存」、または [Pandoc](https://pandoc.org/) 等で `DESIGN.md` を変換してください。

---

## 目次

1. [目的と位置づけ](#1-目的と位置づけ)
2. [システム構成](#2-システム構成)
3. [監視銘柄の解決順](#3-監視銘柄の解決順)
4. [機能一覧](#4-機能一覧)
5. [判断ロジックの詳細](#5-判断ロジックの詳細)
6. [環境変数・外部連携](#6-環境変数外部連携)
7. [依存関係・実行](#7-依存関係実行)
8. [設計上の注意](#8-設計上の注意)

---

## 1. 目的と位置づけ

本システムは **Yahoo Finance の非公式 API** から日本株の相場データを取得し、**ユーザーが定義した条件に合う銘柄を検出して通知する**ためのツール群である。

- **証券会社への発注機能はない**（スクリーニング・通知・検証用途）。
- 中核は **`yahoo_kabu_watch.py`**（監視・Replay・朝スクリーニング・1分足キャッシュ・集計が集約）。
- 補助は **`discord_issue_bot.py`**（Discord から GitHub Issue 作成、`watchlist.json` 更新など）。

**注意:** 非公式 API のため仕様変更・レート制限で動作が変わる可能性がある。1分足の取得可能期間は実質 **直近約30日** 程度とコード上も想定されている。

---

## 2. システム構成

| 構成要素 | 役割 |
|----------|------|
| Yahoo Finance API | クォート、1分足、VWAP、5日平均出来高、日足（MA25 用）など |
| `yahoo_kabu_watch.py` | リアルタイム監視、過去1分足 Replay、朝スクリーニング、CSV キャッシュ、サマリ出力 |
| `discord_issue_bot.py` | `discord.py` ベースの Bot（Issue 作成、`!watch` 等） |
| `watchlist.json` | 監視銘柄の「正」（存在すれば毎ループ読み直し） |
| `symbols.csv` | 銘柄一覧（朝スクリーニングでは最優先で読む） |
| `configs/*.json` | Replay 用戦略（早期撤退・後場・エントリーフィルタ） |
| `data/intraday_1m/` | 日付・銘柄別 1分足 CSV（Replay・検証） |
| `results/` | Replay サマリ、VWAP sweep、`symbol_scores_latest.json` 等 |

---

## 3. 監視銘柄の解決順

### 3.1 リアルタイム監視（`yahoo_kabu_watch.py` メインループ）

1. `--watch-file` があればそのファイル（1行1銘柄）
2. なければ `--watch`（カンマ区切り）
3. どちらもなければ **`watchlist.json` が存在すれば常にそれを優先**（壊れた JSON のときは前回リスト維持）
4. `watchlist.json` が無ければ **`symbols.csv` → スクリプト先頭の `WATCH` 定数**

### 3.2 朝スクリーニング（`--morning-screen`）

**別ルール:** `symbols.csv` があれば最優先 → なければ `watchlist.json` → なければ `WATCH`。

---

## 4. 機能一覧

| 機能 | 起動の目安 | 概要 |
|------|------------|------|
| リアルタイム監視 | `python yahoo_kabu_watch.py` | 既定間隔（デフォルト 1秒）で取得・判定・（任意で）Discord 通知 |
| 朝スクリーニング | `--morning-screen` | 寄り前の候補スコアリング、上位10銘柄を出力 |
| Replay | `--replay` | 1分足を再生し判定・期待値集計・結果ファイル出力 |
| 1分足 EOD 保存 | `--save-intraday-1m-eod` | 当日 1分足を `data/intraday_1m/` に CSV 保存 |
| キャッシュレポート | `--intraday-1m-cache-report-only` | ローカル CSV のカバレッジのみ集計 |
| VWAP sweep | `--vwap-distance-sweep` | 閾値グリッドで Replay を回し比較表を保存 |
| Discord Issue Bot | `discord_issue_bot.py` | `!issue` / `!watch` 等（別プロセス） |

**Discord 通知の運用方針（監視側）**

- 新しく候補に入った銘柄のみ「条件一致」通知（連続スパム防止）。
- 候補から外れたら **連続 3 ループ不一致**（`EXIT_CONFIRM_COUNT`）で条件外れ通知。
- Entry / Stop / Take の水準が **% または円**で大きく変わったら再通知。

---

## 5. 判断ロジックの詳細

**重要:** **実時間監視**と **Replay 内の候補ゲート**は同一ではない。詳細は [8. 設計上の注意](#8-設計上の注意) を参照。

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
- TOPIX ETF（`INDEX_TOPIX_ETF`）の騰落率: **≤−1.5%** で `TOPIX_CRASH`、**(−1.5%, −0.5%]** で `TOPIX_WEAK`
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
- **`--one-trade-per-symbol-per-day`:** 同一銘柄は JST 1 日 1 回まで採用。
- **`risk_controls.daily_loss_stop`:** 当日累積損益が閾値を超えたら新規停止。

**CRASH 時:** 市場全体を即停止するのではなく、**`crash_blocked` 等のフラグと集計**で扱い、シグナルは記録される設計（分析用）。

### 5.7 Replay — ポジション解消（`ReplaySignalEval`）

- **早期撤退（有効時）:** 部分利確前でも VWAP 割れ / 直近5分安値割れで決着。
- **ストップ:** Entry の −2% を最終防衛。
- **トレーリング:** シグナル価格 +1% で部分利確相当フラグの後、VWAP 割れまたは直近5分安値割れで手仕舞い。

**ADD1/ADD2**（`--enable-add`）は別ルールの固定利幅（`exit_style="fixed"`）で検証用。

---

## 6. 環境変数・外部連携

### `yahoo_kabu_watch.py`

- `DISCORD_WEBHOOK_URL` — Webhook 通知
- `ALERT_CHANNEL_ID` + `DISCORD_TOKEN`（旧 `DISCORD_BOT_TOKEN` 互換）— Bot 投稿

### `discord_issue_bot.py`

- `DISCORD_TOKEN`, `GITHUB_TOKEN`, `DISCORD_WEBHOOK_URL`（必須）
- `CONTROL_CHANNEL_ID`, `ALERT_CHANNEL_ID`（任意）

---

## 7. 依存関係・実行

- Python **3.10+** 想定、`requests` 必須。Issue Bot は `discord.py` 等（`requirements.txt` に従う）。

```text
python yahoo_kabu_watch.py
python yahoo_kabu_watch.py --morning-screen
python yahoo_kabu_watch.py --replay --replay-config configs/replay_safe.json
```

---

## 8. 設計上の注意

1. **実時間の候補条件と Replay の候補条件は完全一致しない**（Replay は「出来高増加必須」「Entry 接近率」まで要求しないブロックがある）。検証結果と実アラートの差に注意すること。
2. **地合い NORMAL/WEAK/CRASH は Replay 中心**であり、実時間メインループには組み込まれていない。
3. **非公式 API** 依存のため、欠損フィールド・レート制限への耐性が運用上重要。
4. ファイル先頭の **`WEAK_ENTRY_*` 等**は意図としての定数が残る一方、現行の `_quality_rejects` 経路では **WEAK 時は RSI/ATR/前場限定**などが中心である。

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
| 2026-05-09 | TODOを `docs/TODO.md` に分離 |
