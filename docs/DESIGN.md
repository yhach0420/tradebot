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
7. [環境変数・外部連携](#7-環境変数外部連携)（paper_trade の Discord・成果物・runtime は **§7.4**）
8. [依存関係・実行](#8-依存関係実行)（**§8.1** Windows watchdog）
9. [設計上の注意](#9-設計上の注意)
10. [実装工程のトピック（これまでの経緯）](#10-実装工程のトピック)
11. [コマンドライン引数・実行分岐（`yahoo_kabu_watch.py`）](#11-コマンドライン引数実行分岐yahoo_kabu_watch)
12. [成果物の命名とディレクトリ規則](#12-成果物の命名とディレクトリ規則)
13. [continuation-v1: 共有エンジン・paper dry-run・Phase2 shadow](#13-continuation-v1-共有エンジンペーパードライランphase2-shadow)（**§13.1** 現実装の整理／**§13.2** 目標アーキテクチャ）

---

## 1. 目的と位置づけ {#1-目的と位置づけ}

本システムは **Yahoo Finance の非公式 API** から日本株の相場データを取得し、**ユーザーが定義した条件に合う銘柄を検出して通知する**ためのツール群である。

- **証券会社への発注機能はない**（スクリーニング・通知・検証用途）。
- 中核は **`yahoo_kabu_watch.py`**（監視・Replay・朝スクリーニング・1分足キャッシュ・集計が集約）。
- 補助は **`discord_issue_bot/discord_issue_bot.py`**（Discord から GitHub Issue 作成、`watchlist.json` 更新など）。

**注意:** 非公式 API のため仕様変更・レート制限で動作が変わる可能性がある。1分足の取得可能期間は実質 **直近約30日** 程度とコード上も想定されている。

---

## 2. システム構成 {#2-システム構成}

| 構成要素 | 役割 |
|----------|------|
| Yahoo Finance API | クォート、1分足、VWAP、5日平均出来高、日足（MA25 用）など |
| `yahoo_kabu_watch.py` | リアルタイム監視、過去1分足 Replay、朝スクリーニング、CSV キャッシュ、サマリ出力 |
| `discord_issue_bot/discord_issue_bot.py` | `discord.py` ベースの Bot（Issue 作成、`!watch` 等） |
| `watchlist.json` | 監視銘柄の「正」（存在すれば毎ループ読み直し） |
| `symbols.csv` | 銘柄一覧（朝スクリーニングでは最優先で読む） |
| `configs/*.json` | Replay 用戦略（早期撤退・後場・`entry_filters` / `regime_filters` / `signal_filters` / `composite_signal_filters` / `regime_controls` 等） |
| `configs/regime_filter_sweep/` | `--regime-filter-sweep` / `--topix-weak-threshold-sweep` が自動生成する比較用 JSON |
| `configs/signal_filter_sweep/` | `--signal-filter-sweep` が自動生成する比較用 JSON |
| `configs/composite_filter_sweep/` 他 | `--composite-filter-sweep` 等の各 sweep が出力する比較用 JSON（`weak_combo_filter_sweep` / `strong_risk_filter_sweep` / `rising_ratio_threshold_sweep` / `auto_block_momentum_sweep` 等） |
| `configs/forward_split_periods.json` | `--replay-range forward_split` 用の **train / validation / forward** の日付集合（`--forward-split-periods-path` で上書き可） |
| `data/intraday_1m/` | 日付・銘柄別 1分足 CSV（Replay・検証）。EOD 保存は **`data/intraday_1m/<YYYY-MM-DD>/<銘柄>.csv`**。 |
| `results/` | **ルート直下:** `symbol_scores_latest.json`（Replay 生成）、`vwap_sweep_summary_<時刻>.txt`（VWAP sweep の一覧サマリ・後方互換）など。**日付階層:** Replay・各種 sweep の主出力は **`results/YYYYMMDD/<カテゴリ>_<タイムスタンプ>/`**（`_build_results_output_dir` / `_build_results_dir_from_output_subdir`。`YYYYMMDD` は `batch_stamp` または `sweep_stamp` の先頭8桁）。スイープのセルはさらにサブフォルダに分岐する。`paper_trade` は **`results/paper_trade/YYYYMMDD/`** で従来と同じ。 |
| `tools/md_to_pdf.py` | `docs/DESIGN.md` → PDF など（日本語フォント埋め込み済みスクリプト）。 |
| `scripts/migrate_results_to_date_folders.py` | 旧 **`results/` 直下**に散在した replay/sweep フォルダを **`results/YYYYMMDD/`** へ移す移行用（`paper_trade`・日付バケット・`symbol_scores_latest.json` は触らない）。 |
| `scripts/watchdog.py` + `scripts/start_*.bat` + `scripts/run_issue_bot_inner.bat` + `scripts/check_issue_bot_running.ps1` + `scripts/run_watchdog_inner.bat` + `scripts/check_watchdog_running.ps1` | **Windows 向け自動復帰**（`psutil` で `discord_issue_bot` / `paper_trade` を監視）。watchdog は **inner bat + 絶対パス `python`** でタスク スケジューラの PATH/cwd 差に強い。ログは **`logs/runtime/`**（**`watchdog_launcher_*.log`** / **`watchdog_*.log`** 等）。運用手順は **README.md** と **§8.1**。 |

**`watchlist.json` が二箇所あることに注意:** 監視本番（`yahoo_kabu_watch.py`）は **リポジトリルート**の `watchlist.json` を読む。Discord Issue Bot の `!watch` は **`discord_issue_bot/watchlist.json`** を読み書きする（`discord_issue_bot.py` 内の `BASE_DIR` 基準）。運用で両方を同期したい場合はコピーやシンボリックリンクで揃える。

**固定ランダム日プール（`FIXED_REPLAY_RANDOM_POOLS`）:** `random_feb` / `random_mar` / `random_mar_cache_only` / `random_apr` / `random_60d` は、コード先頭の **暦日レンジ**から平日文字列を生成してランダム抽出の母集団にする（実際の営業日・キャッシュ有無は実行時に検証）。

**sweep 用 Replay レンジ定数 `SWEEP_REPLAY_RANGES`:** 多くの一括 sweep は **`random_apr` のみ**を内部レンジとして使う（同一データセット上での AB 比較を優先）。`--strong-trend-quality-validation-sweep` 等は例外で `random_mar` / `random_60d` を跨ぐ。

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
| 1分足 EOD 保存 | `--save-intraday-1m-eod` | 当日（または `--intraday-1m-eod-date`）の 1分足を `data/intraday_1m/` に保存。`--force-intraday-1m-eod-time` で引け前検証可 |
| キャッシュレポート | `--intraday-1m-cache-report-only` | ローカル CSV のカバレッジのみ集計 |
| Replay（モード） | `--replay-mode` | `normal`（従来・待機あり）または `fast`（待機なし・集計優先）。`--replay-fast-discord` / `--replay-fast-verbose` / `--replay-fast-print-signal-details` で詳細度を上書き |
| Replay（日付範囲） | `--replay-range …` | `1d`〜`60d`、**`random_5d` / `random_feb` / `random_mar` / `random_apr` / `random_mar_cache_only` / `random_60d`** 等のランダム営業日抽出、**`forward_split`**（`forward_split_periods.json` の全日候補＋`--replay-random-days` でサブサンプル可）。**`--replay-date` 指定時は日付リストのみこのフラグ群をバイパス**（下段参照）。 |
| Replay（単日・JST 固定） | `--replay-date YYYY-MM-DD`（`--replay` と併用） | **実装済み**（`parse_args` → `run_replay`）。**その JST 日だけ**を再生。`--replay-range` のランダム抽選・**`forward_split`** の候補集合は使わない。表示ラベルは **`fixed_<日付>`**、既定の成果物ディレクトリは **`results/YYYYMMDD/replay_fixed_<日付>_<batch_stamp>/`**。`--replay-repeat` が **1** のときの詳細ログ stem は **`replay_<時刻JST>_range-fixed_<日付>`**（§12）。`--replay-repeat` で同一日を複数回可。**`--replay-range` は単日リストを上書きしないが、Yahoo 1分足取得の `range` 引数としては引き続き効く**（過去日を再生する場合、既定 `1d` では窓が足りないことがあるため **`60d` 等への変更を検討**）。 |
| Forward 分割検証 | `--forward-split-validation` | train のみから危険核を抽出し validation/forward で再登場分析（**AUTO_BLOCK や `excluded_from_eval` は変更しない**）。`--replay-range forward_split` 推奨 |
| ペーパートレード（live） | `--paper-trade` | 実注文なしで **live Yahoo** を間欠取得し CSV/TXT へ記録。**`run_paper_trade` のみ**（**`run_replay` は呼ばない**）。中核シグナル〜出口は continuation-v1 の **共有 engine／paper position exec** と整合（§13）。 |
| paper_trade dry-run（replay 経由） | `--paper-trade-dry-run-replay` + `--paper-trade-dry-run-day YYYY-MM-DD` | **`--paper-trade` / `--replay` と排他**。`data/intraday_1m/<その日>/` の CSV 銘柄を **`run_replay`（fast・単日・`paper_trade_mode`）** で再生し、`results/paper_trade_dry_run/<YYYYMMDD>/` ほか **`results/<YYYYMMDD>/paper_trade_dry_run_<YYYYMMDD>/`** に成果を出す（§11.1・§13.6）。 |
| VWAP sweep | `--vwap-distance-sweep` | VWAP 乖離 `entry_filters` 閾値グリッドで Replay を回し比較表を保存 |
| daily_loss_stop sweep | `--daily-loss-stop-sweep` | 当日損失ストップ ON/OFF・閾値（例: 30k/50k/70k 円/100株）を比較 |
| regime filter sweep | `--regime-filter-sweep` | `regime_filters`（朝弱・上昇銘柄割合・TOPIX_WEAK）の組合せを比較 |
| TOPIX WEAK 閾値 sweep | `--topix-weak-threshold-sweep` | `TOPIX_WEAK` 判定の `topix_weak_threshold_pct`（例: −0.2〜−0.7%）を比較 |
| signal filter sweep | `--signal-filter-sweep` | `signal_filters`（ギャップ・VWAP乖離・時刻）の AB 比較 |
| forward risk 仮想ブロック sweep | `--forward-risk-virtual-block-sweep` | forward risk 構造に対し STRONG 系仮想ブロックを **仮想集計のみ**で比較（§4.1）。`--replay-range` 等は通常 Replay と同様 |
| その他 sweep（一覧） | 下表 **§4.1** | composite / STRONG 系 / rising 閾値 / weak combo / AUTO_BLOCK 拡張など（上記 **forward risk** 以外も含む） |
| Discord Issue Bot | `discord_issue_bot/discord_issue_bot.py` | `!issue` / `!watch` 等（別プロセス） |

### 4.1 一括比較（sweep）CLI と出力先

多くの sweep は内部で **`random_apr`** を用いる（コード上 `SWEEP_REPLAY_RANGES`）。**各 sweep の Replay 回数 `n_repeat` は一律ではない**（§8・§11.3 参照）。`--replay-seed` で再現性を制御。例外として **`--strong-trend-quality-validation-sweep`** は `random_apr` / `random_mar` / `random_60d` を跨いで検証する。

**出力レイアウト（現行）:** Replay・sweep の成果物の多くは **`results/YYYYMMDD/...`** に保存される（`yahoo_kabu_watch.py` の `_results_run_date_folder_from_timestamp` 等）。コンソールの `output_subdir: results/...` ログは **プロジェクトからの相対パス**であり、先頭の **`YYYYMMDD`** が省略されている場合があるので、実ディスクでは **`results`** 直下に日付フォルダを挟む形で解釈する。**`--replay-date`** 利用時は `run_replay` 内の表示用 `replay_range_label` が **`fixed_<YYYY-MM-DD>`** となる。`main` の `--replay` バッチでは `replay_output_subdir` に **`replay_<repeat_label>_<batch_stamp>`**（例: `replay_fixed_2026-05-12_20260513_000114`）を渡し、`_resolve_replay_results_dir` が **`_split_compound_results_slug`** により **`results/YYYYMMDD/replay_<repeat_label>_<batch_stamp>/`** を生成する（**ユーザー向け CLI でこの文字列を直接指定するオプションは無い**）。**`--replay-repeat` が 1 のとき**、フォルダ内の詳細ログの stem は **`replay_<保存時刻JST>_range-fixed_<日付>`**（`range-` に続けて **そのまま `replay_range_label`** が付く）となる（§12）。

| CLI フラグ | 出力の目安（先頭はいずれも **`results/YYYYMMDD/`**） |
|------------|--------------------------------------------------------|
| `--vwap-distance-sweep` | `vwap_sweep_<時刻>/` 以下に閾値×range ごとのセル。**併せて** `results/vwap_sweep_summary_<時刻>.txt` をルートに保存（一覧用）。 |
| `--daily-loss-stop-sweep` | `daily_loss_stop_sweep_<時刻>/` |
| `--regime-filter-sweep` | `regime_filter_sweep_<時刻>/` |
| `--topix-weak-threshold-sweep` | `topix_weak_threshold_sweep_<時刻>/` |
| `--signal-filter-sweep` | `signal_filter_sweep_<時刻>/` 以下に config×range のネスト |
| `--composite-filter-sweep` | `composite_filter_sweep_<時刻>/` |
| `--regime-control-sweep` | `regime_control_sweep_<時刻>/` |
| `--weak-risk-filter-sweep` | `weak_risk_filter_sweep_<時刻>/` |
| `--strong-risk-filter-sweep` | `strong_risk_filter_sweep_<時刻>/` |
| `--strong-combo-filter-sweep` | `strong_combo_filter_sweep_<時刻>/` |
| `--strong-trend-quality-sweep` | `strong_trend_quality_sweep_<時刻>/` |
| `--strong-trend-quality-validation-sweep` | `strong_trend_quality_validation_sweep_<時刻>/` |
| `--rising-lt50-validation-sweep` | `rising_lt50_validation_sweep_<時刻>/` |
| `--rising-ratio-threshold-sweep` | `rising_ratio_threshold_sweep_<時刻>/` |
| `--weak-combo-filter-sweep` | `weak_combo_filter_sweep_<時刻>/` |
| `--auto-block-momentum-sweep` | `auto_block_momentum_sweep_<時刻>/` |
| `--strong-extension-threshold-sweep` | `strong_extension_threshold_sweep_<時刻>/`（JSON 併記。通常 Replay 合算 JSON にも `extension_robustness_metrics` 等が付く経路あり） |
| `--forward-risk-virtual-block-sweep` | `forward_risk_virtual_block_sweep_<時刻>/`（`sweep_summary.txt`・`forward_risk_virtual_block_sweep.json`。**AUTO_BLOCK / `excluded_from_eval` は変更しない** analysis-only） |

### 4.2 Discord 通知の運用方針（監視側）

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

### 5.5 Replay — 地合いレジーム（`STRONG` / `NORMAL` / `WEAK` / `CRASH`）

各ステップでウォッチ全体に対し 1 回判定する。

**主な `market_reasons` の例**

- 日経 ETF（`INDEX_NIKKEI_ETF`）が日中 VWAP より下
- TOPIX ETF（`INDEX_TOPIX_ETF`）の騰落率（前日終値から再計算）: **≤ −1.5%** で `TOPIX_CRASH`。**−1.5% より上で、かつ `topix_weak_threshold_pct` 以下**（上限 inclusive）で `TOPIX_WEAK`。閾値は `regime_filters.topix_weak_threshold_pct` で指定でき、**未指定時は `WEAK_TOPIX_CHG_PCT_MAX`（−0.5%）** と同じ値を使う。
- 上昇銘柄割合 < `MARKET_RISING_RATIO_MIN`（0.40）
- 直近30分の解決済みシグナル失敗率 > `MARKET_ENTRY_FAIL_RATE_30M_MAX`（0.60、解決数≥3）
- 高値付近銘柄割合 < `MARKET_HIGH_UPDATE_RATIO_MIN`（0.07）
- 12:30–14:00 の **後場弱** 条件（前場高値ブレイク率・VWAP 下割合・指数弱さの組合せ）

**レジーム決定（実装）**

- **`CRASH`:** TOPIX が **−1.5% 以下**（異常値ガード: 騰落率の絶対値が **20% 以下** のときのみ採用）かつ `crash` フラグ。
- **`WEAK`:** `CRASH` でなく、`market_reasons` が **空でない** とき。
- **`STRONG`:** `CRASH`/`WEAK` でなく、TOPIX 騰落率（再計算）が **`STRONG_TOPIX_CHG_PCT_MIN`（0.30%）以上** かつ **fallback 未使用**などコード上の条件を満たすとき。
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
  - `rising_ratio_threshold_pct` **指定時**: 上昇銘柄割合がその値**未満**なら `REGIME_FILTER_RISING_LT_THR`（`disable_rising_ratio_lt50` とは独立に評価）。
  - **未指定**かつ `disable_rising_ratio_lt50` が **true**: 上昇銘柄割合 **50%未満** で `REGIME_FILTER_RISING_LT50`。
  - `disable_topix_weak`: **true** かつ（`topix_chg ≤ topix_weak_threshold_pct` **または** `market_reasons` に `TOPIX_WEAK`）なら `REGIME_FILTER_TOPIX_WEAK`。
- **`signal_filters`（config・Replay）:** 同様に `crossed` 後に評価され、該当すれば集計対象外。
  - `disable_entry_after_hhmm` + `entry_after_hhmm`（既定 **10:30**）: **true** のとき、シグナル時刻がその **JST 時刻以降** なら `SIGNAL_FILTER_ENTRY_AFTER_HHMM`。
  - `disable_gap_ge_pct` + `gap_ge_threshold_pct`（既定 **3.0%**）: **true** のとき、当日始値ギャップ（前日終値比）が **閾値以上** なら `SIGNAL_FILTER_GAP_GE`。
  - `disable_vwap_distance_ge_pct` + `vwap_distance_ge_threshold_pct`（既定 **1.5%**）: **true** のとき、VWAP 乖離率 **≥ 閾値** なら `SIGNAL_FILTER_VWAP_DIST_GE`（`entry_filters` の絶対値上限とは別ルート）。
- **`composite_signal_filters`（config・Replay）:** `crossed` 後に評価。**`weak_combo_filter` / `weak_risk_filter` / `strong_risk_filter` / `strong_combo_filter`** 等でマッチすれば集計対象外（理由タグ・仮想PnL分析用メタが付く）。詳細は **§6.13**。
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
| `STRONG_TOPIX_CHG_PCT_MIN` | **0.30** | **弱理由が空**で TOPIX が **0.30% 以上** のとき **`STRONG`** レジーム判定に使う（§5.5）。 |
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

**レジーム集約:** **`CRASH`** は **TOPIX ≤ −1.5%**（異常値ガードあり）のとき。**`WEAK`** はそれ以外で `market_reasons` が空でないとき。**`STRONG`** / **`NORMAL`** は §5.5 の優先順位に従う。`TOPIX_WEAK` の帯の上限は **`regime_filters.topix_weak_threshold_pct`** があればそれ、なければ **`WEAK_TOPIX_CHG_PCT_MAX`（−0.5%）**。

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
| `rising_ratio_threshold_pct` | number または省略 | 指定時は上昇銘柄割合がこの値（**% または小数**、実装で正規化）**未満**なら `REGIME_FILTER_RISING_LT_THR`（`disable_rising_ratio_lt50` とは独立）。**省略時**はこの経路は使わず、`disable_rising_ratio_lt50` のみが **50%未満**判定に効く（§5.6）。 |

### 6.12 Replay config — `signal_filters`（任意）

JSON 直下の **`signal_filters`**（省略可）。`_apply_replay_config_to_flags` が読み、**すべて省略可**・既定はフィルタ無効。

| キー | 型・既定 | 意味 |
|------|----------|------|
| `disable_gap_ge_pct` | bool, **false** | **true** のとき、始値ギャップ（前日終値比）が **`gap_ge_threshold_pct`（既定 3.0%）以上** ならシグナルを集計対象外。 |
| `gap_ge_threshold_pct` | number, **3.0** | ギャップ％のしきい値。 |
| `disable_vwap_distance_ge_pct` | bool, **false** | **true** のとき、VWAP 乖離率 **≥ `vwap_distance_ge_threshold_pct`（既定 1.5%）** なら集計対象外（上方向の「追い過ぎ」抑制）。 |
| `vwap_distance_ge_threshold_pct` | number, **1.5** | 上記のしきい値（%）。 |
| `disable_entry_after_hhmm` | bool, **false** | **true** のとき、シグナル時刻が **`entry_after_hhmm` 以降（JST）** なら集計対象外。 |
| `entry_after_hhmm` | string, **"10:30"** | 例: `"10:30"`。 |

**命名の注意:** `regime_filters` / `signal_filters` のキー名は `disable_*` だが、値が **true** のとき **フィルタが掛かり** 該当シグナルは **`excluded_from_eval`** になる（「disable = その特徴を無効化するのではなく、その条件のエントリーを止める」意味合いに近い）。

### 6.13 Replay config — `composite_signal_filters`（任意）

Replay JSON 直下の **`composite_signal_filters`**（省略可）。**`enabled: true`** なサブブロックのみ有効。代表例:

| サブキー | 概要 |
|----------|------|
| `weak_combo_filter` | `market_regime` 別に、VWAP 乖離下限・高値更新回数（entry 直前）等の **AND 条件**を `block_conditions` で列挙。**いずれかの行がマッチしたら除外（OR）**。 |
| `weak_risk_filter` | **WEAK** 時に VWAP 距離・ギャップの「危険帯」だけを除外する複合。 |
| `strong_risk_filter` | **STRONG** 時に VWAP 乖離が閾値以上の ENTRY を除外。 |
| `strong_combo_filter` | **STRONG** 時に高値更新回数 × VWAP 距離などの組合せで除外。 |

AUTO_BLOCK 系（`auto_block_strong_chase_after_extension_enabled` 等）は **既定 OFF** とし、sweep・`extension_*` 分析で効果を見てから有効化する想定（§10.7 参照）。

### 6.14 Replay config — `regime_controls`（任意）

| キー | 概要 |
|------|------|
| `enabled` | **true** のときのみ、`STRONG` / `NORMAL` / `WEAK` / `CRASH` 各キー配下の **`entry_enabled`**、**`max_gap_pct`**、**`max_vwap_distance_pct`**、**`exit_mode`**（`normal` / `fast`）を Replay のエントリー・ADD 判断に反映。 |

### 6.15 Replay config — `risk_controls`（任意）

JSON 直下の **`risk_controls`**（省略可）。代表として **`daily_loss_stop`** サブブロック:

| キー | 意味 |
|------|------|
| `enabled` | **true** のとき、当日累積損益（100株換算）が **−`stop_yen_100_shares` 円以下**で新規 ENTRY/ADD を抑止。 |
| `stop_yen_100_shares` | 閾値（円、**100株ベース**）。省略時はフラグ適用ロジック側の既定（例: **50,000**）に寄せる。 |

### 6.16 Replay config — `paper_trade`（paper_trade モード専用・任意）

`--paper-trade` が読み込む replay JSON の **`paper_trade`** ブロック（省略可）。**売買条件・スコア・AUTO_BLOCK には触れず**、取得・通知の **遅延・タイムアウト・寄り軽量化・（任意）Tier1/Tier2 監視分割**のみを制御します。CLI 引数がある場合は **CLI が優先**。

| キー | 既定 | 意味 |
|------|------|------|
| `lag_metrics_enabled` | **true** | 遅延計測と CSV 列の埋め込み。 |
| `embed_lag_display_enabled` | **true** | live embed に「検出時刻 / 通知時刻 / 遅延」（Replay 専用の「Replay時刻」ラベルは使わない）。 |
| `lag_guard_enabled` | **true** | `signal_lag_sec > max_signal_notify_lag_sec` なら Discord 抑止（`PAPER_LOG` に WARN）。 |
| `max_signal_notify_lag_sec` | **120** | 上記しきい値（秒）。 |
| `fetch_timeouts_enabled` | **false** | **true** のとき per-symbol の Yahoo 取得を別スレッド＋期限付きで打ち切り、poll 予算超過で残銘柄を次 poll へ回す。 |
| `per_symbol_fetch_timeout_sec` | **3** | chart/quote/vwap 系の `requests` タイムアウト目安。 |
| `total_poll_timeout_sec` | **45** | 1 poll の処理上限（秒）。 |
| `max_retry_per_symbol` | **1** | 取得失敗時の追加リトライ回数（本体試行は 1 + この値）。 |
| `opening_light_mode.enabled` | **false** | **true** かつ JST が **`until_hhmm` 未満**のとき、1 分足本数不足を即スキップ（`OPENING_LIGHT_INSUFFICIENT_1M`）。 |
| `opening_light_mode.suppress_discord_signal_notify` | **false** | **true** かつ上記 opening_light 窓内のとき、**signal embed のみ** Discord 送信を抑止（CSV は `OPENING_LIGHT_DISCORD_SUPPRESSED` で残す）。 |
| `dynamic_watchlist` | （§6.17） | **Tier1 軽量スクリーニング + Tier2 重点監視**。省略時は **無効**（従来どおり全銘柄を毎 poll 重処理）。 |
| `tier1_score_weights` | （§6.17） | Tier1 の加重スコア用ウェイト。省略時はコード既定。 |
| `candidate_state_notify_enabled` | **true** | 有力候補の **Entry/Stop/Take 変化**（UPDATE）と **無効化**（INVALIDATED）の Discord / CSV 拡張を有効化。 |
| `price_change_notify_threshold_pct` | **0.5** | UPDATE: E/S/T の **相対変化率(%)の最大**がこの値以上のときのみ embed（同一銘柄は `symbol_notify_cooldown_sec` で抑制）。 |
| `symbol_notify_cooldown_sec` | **180** | 同一銘柄の **候補系 embed**（UPDATE/INVALIDATED）の最短間隔（秒）。 |
| `candidate_vwap_break_invalidate_pct` | **-0.5** | INVALIDATED: 直前まで有力だった銘柄で **VWAP乖離率 ≤ この値(%)** のとき `VWAP_BREAK`。 |

**CLI:** `--max-signal-notify-lag-sec` / `--paper-trade-fetch-timeouts` / `--paper-trade-opening-light` / `--paper-trade-dynamic-watchlist` / `--paper-trade-lag-guard-off`。

**テスト:** `python -m unittest tests.test_paper_trade_lag tests.test_paper_tier_watchlist`。

### 6.17 paper_trade — Tier1 / Tier2 動的ウォッチ（任意）

**目的:** 全銘柄を毎 poll **1m + intraday + MA25** で重く回さず、**広域は軽量 Quote のみ**で順位付けし、**上位のみ Tier2 で従来精度の判定**を行う（リアルタイム性・Yahoo 遅延耐性）。

- **Tier1:** `fetch_quote` のみ（`tier1_*_timeout` で打切り可）。`refresh_sec` ごとに（または Tier2 リスト空のとき）全 `watch` を走査し、加重スコアでソート。成果物: `paper_trade_tier1_snapshot.json`、サマリの `paper_trade_tier1_ranking_preview`。
- **Tier2:** 既存 poll 間隔で **`max_symbols` 本まで** 1m 取得・breakout・Discord・lag guard。`sticky_sec` 内に **crossed_true** した銘柄はウォッチに残しやすく、**全入替えはしない**（`_paper_merge_tier2_watchlist`）。
- **runtime state:** `paper_tier2_symbols` / `paper_signal_sticky_until` / `paper_tier1_vol_ema` 等を日次 JSON に保存し **graceful restart** で復元。

**health / metrics:** `paper_trade_runtime_metrics_summary` に `tier1_symbol_count` / `tier2_symbol_count` / `tier2_rotation_total` / `tier1_budget_hits_session` / `poll_budget_skip_events` / `avg_signal_lag_sec` 等を載せる。日次 `paper_trade_health_report.txt` に **【TIER_MONITOR】** 節を追加。

**Embed:** live 時は **監視層** フィールドに `Tier2（重点監視）` または `全銘柄（従来）` を表示（検出・通知・遅延に続く）。

**移行メモ:** `docs/config_migration_paper_trade_tier12.md`。

---

## 7. 環境変数・外部連携 {#7-環境変数外部連携}

### 7.1 `yahoo_kabu_watch.py`（監視・Replay・paper_trade）

#### 7.1.1 `.env` の読み込み（`yahoo_kabu_watch.py`）

- **読み込みパス:** `Path(__file__).resolve().parent / ".env"`（= **リポジトリ直下の `.env` のみ**）。**親ディレクトリを辿る探索は行わない**。
- **手順:** 雛形は **`.env.example`** をコピーして編集する想定。
- **実装:** `python-dotenv` が **インストール済み**のときだけ `load_dotenv(..., override=False)` を実行。**未導入の場合**は `.env` を読まず、**OS の環境変数**のみが有効（起動時に stderr で `python-dotenv not installed` 相当の注意が出る経路あり）。
- **Issue Bot 側の `.env`:** パス規則は **別**（**§7.2**）。混同しないこと。

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `DISCORD_WEBHOOK_URL` | 任意 | 条件一致・突破などの **Embed Webhook** 通知。未設定なら Webhook 経路はスキップ。 |
| `DISCORD_TOKEN` | Bot 投稿時（推奨名） | **`ALERT_CHANNEL_ID` / `PAPER_*` 利用時**の Discord Bot 投稿で参照する **正式な Bot トークン名**。 |
| `DISCORD_BOT_TOKEN` | 互換のみ | **後方互換**の旧名。`DISCORD_TOKEN` が空のときだけ読み取りに使われ、**移行警告**が出る経路がある（**廃止予定**の扱い）。 |
| `ALERT_CHANNEL_ID` | 任意 | 数値チャンネル ID。設定時は Bot が **そのチャンネル**へ投稿（トークン必須）。 |
| `PAPER_ALERT_CHANNEL_ID` | 任意 | **paper_trade** 時の **アラート系**投稿先（**未設定なら `ALERT_CHANNEL_ID` にフォールバック**）。**送受の内訳は §7.4.1**。 |
| `PAPER_LOG_CHANNEL_ID` | 任意 | **paper_trade** 時の **運用ログ系**投稿先（Bot のみ。**未設定のときは Discord ログチャンネルへは送らず**ターミナルのみ）。**内訳は §7.4.1**。`--paper-trade-disable-discord-logs` で抑止可。 |

### 7.2 `discord_issue_bot/discord_issue_bot.py`

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `DISCORD_TOKEN` | **必須** | Bot ログイン。 |
| `GITHUB_TOKEN` | **必須** | GitHub REST API で Issue 作成（`repo` 権限）。 |
| `DISCORD_WEBHOOK_URL` | 条件付き必須 | **`ALERT_CHANNEL_ID` 未設定時**は Webhook 通知に必須（`!issue` 実行時の検証）。 |
| `CONTROL_CHANNEL_ID` | 任意 | 設定時は **`!issue` / `!watch`** はこのチャンネルからのみ有効。未設定なら **全チャンネルで制御コマンド可**（運用では指定推奨）。 |
| `ALERT_CHANNEL_ID` | 任意 | 設定時は Issue 作成後の要約を **Bot の `send`** でこのチャンネルへ。未設定時は Webhook にフォールバック。 |

**GitHub リポジトリ先（コード定数）:** `GITHUB_OWNER` / `GITHUB_REPO` は **`discord_issue_bot/discord_issue_bot.py` 先頭の定数**（例: `yhach0420` / `tradebot`）。別リポジトリへ変えたい場合は **コードを編集**する。

**`.env`:** Issue Bot は **`discord_issue_bot/.env` のみ**を読む（親ディレクトリは探索しない）。`python-dotenv` 未インストール時は読み飛ばし。

### 7.3 Discord Issue Bot — コマンド（`!` プレフィックス）

| コマンド | 制約 | 動作 |
|----------|------|------|
| `!issue <本文>` | `CONTROL_CHANNEL_ID` 設定時は **そのチャンネルのみ** | GitHub Issue 作成、結果を ALERT または Webhook へ。 |
| `!watch add <銘柄>` | 同上 | `discord_issue_bot/watchlist.json` に追加。 |
| `!watch remove <銘柄>` | 同上 | 同上から削除。 |
| `!watch list` | 同上 | 現在の監視一覧を表示。 |

### 7.4 paper_trade 運用仕様 {#74-paper_trade運用仕様}

**live `--paper-trade`** は **`run_paper_trade`** を起動する。**`run_replay` は呼ばない**（Yahoo スナップショット経路。§7.4.1 の Discord もここに限定）。**`--paper-trade-dry-run-replay`** は **§13.6** のとおり **`run_replay` を1回呼ぶ**（本節の LOG/ALERT 説明の対象外）。CLI ヘルプに近い文言が残っていても、**本節は live paper を主に正**とする。

#### 7.4.1 Discord チャンネル分担（`PAPER_ALERT_CHANNEL_ID` / `PAPER_LOG_CHANNEL_ID`）

実装は **`_paper_send_signal_embed`（Embed）**、**`_paper_send_log`（LOG 専用）**、**`_paper_send_alert_ops_line`（EOD / health 通知用のプレーンテキスト。alerts ON なら ALERT、OFF かつ logs ON なら LOG へフォールバック）** の組み合わせ。**`DISCORD_WEBHOOK_URL` が設定されている場合**、Bot チャンネルが無くても **Embed を Webhook 経由で送る**経路がある（`_paper_send_signal_embed` / `_paper_send_alert` の引数）。

**`PAPER_ALERT_CHANNEL_ID`（未設定時は `ALERT_CHANNEL_ID` へフォールバック。Webhook 併用の可否は実装と同じ）へ、`run_paper_trade` から送るものは次の 3 種類に限定される。**

1. **🚀 Entry上抜け**の **Embed**（`build_embed_entry_cross`。**重複キー**は `paper_trade_seen_alerts.json` で抑制）。
2. **引け後**の **`[PAPER] day finished`** の **1行テキスト**（`_paper_send_alert_ops_line` → `_paper_send_alert`）。
3. **`[PAPER] health report generated …`** の **1行テキスト**（同上。health 本文ファイルは送らない）。

**通常監視ループ（リアルタイム監視）側には存在するが、現行 `run_paper_trade` では通常送信しないもの**

- **🟡 候補価格変更** embed（`build_embed_levels_change` 系の経路）。
- **`[SHADOW:*]`** をタイトルに付与した embed（Replay 本番ループ側の shadow 通知経路）。

**`PAPER_ALERT` には送らず、`PAPER_LOG` またはターミナルのみになるもの（実装）**

- **`poll#…` 見出しの poll digest**、**pipeline counter 行**、**deprecated 注釈行**。
- **`runtime_metrics` 行**。
- **開場前 `market not open`** の sleep メッセージ。
- **銘柄別 `fetch_failed`** の警告行。
- **起動時 self check** のブロック。
- **`[PAPER][DEBUG] …`**（**ターミナルのみ**）。

**`PAPER_LOG_CHANNEL_ID` に送るもの（`discord_logs_enabled` かつチャンネル・トークンが有効なとき）**

- **poll digest**（`[paper_trade] poll #N …` ヘッダ＋カウンタ・注釈）。
- **各 poll 末尾の OK 行**（`poll#… fetched=…` 等）。
- **`market_state:` 行**。
- **`runtime_metrics` 行**（poll / fetch の集計 latencies 等）。
- **起動時 self check**（`.env` 読込状態・チャンネル set/unset・`config_hash` 一行・ロック状態など）。
- **取得系 WARN**（例: **連続 fetch 失敗**、**slow_poll**、**fetch 平均遅延**、**例外型カウントの閾値越え**通知）。
- **開場前**の `market not open` メッセージ。
- **引け後**の **`post_close_pass`** 付き poll（銘柄ループを飛ばすが、digest は LOG に残る）。
- **Ctrl+C 終了**時の shutdown 一行。

**`PAPER_LOG` に送らないもの**

- **Entry 上抜け Embed**（ALERT 側専用）。
- **health レポート本文**（ファイル **`paper_trade_health_report.txt`** にのみ保存。Discord では **生成通知の1行**が ALERT／（alerts OFF 時）LOG のどちらか）。

#### 7.4.2 quiet モード（CLI と Discord の対応）

| フラグ | 効果 |
|--------|------|
| `--paper-trade-disable-discord-alerts` | **`PAPER_ALERT` へ送っていたもの**（Entry Embed・**EOD 1行**・**health 生成通知1行**）を **Discord では出さない**。**CSV / TXT ファイル出力は継続**。`--paper-trade-disable-discord-logs` が付いていない場合、**EOD / health 通知行は `PAPER_LOG` のみ**へ送る（`_paper_send_alert_ops_line` のフォールバック）。 |
| `--paper-trade-disable-discord-logs` | **`PAPER_LOG` へ送っていたテキスト**（poll digest・runtime・起動チェック・市場／取得 WARN 等）を **Discord では出さない**（ターミナルは継続）。 |

#### 7.4.3 `results/paper_trade/YYYYMMDD/` 成果物一覧

| ファイル | 役割（1行） |
|----------|-------------|
| `paper_trade_log.csv` | 各 poll での銘柄別スナップショット行を **追記**（スクリーニング・crossed・遅延列・**候補の previous_entry / change_pct / invalidated_reason** 等）。 |
| `paper_trade_summary.txt` | 直近 poll の **`paper_trade_summary` 形式**のテキストサマリ（パイプラインカウンタ・fetch 状態・skip_reason 集計等）。**`notify_sent_count` の意味は下記 §7.4.3.1**。 |
| `paper_trade_health_report.txt` | 日次終了時の **runtime health**（**`runtime_status`**・遅延・fetch 失敗率・例外集計・判定理由の箇条書き）。**Discord には全文転送されない**。 |
| `paper_trade_runtime_state.json` | **graceful restart** 向けに、poll 累計・遅延 streak・cooldown・最終時刻・**Tier2 ウォッチ／sticky／Tier1 出来高 EMA** 等を保存。**同一 `state_day_key` のときのみ**起動時に復元。 |
| `paper_trade_tier1_snapshot.json` | **Tier1** 更新時の **上位スコア・内訳**（`dynamic_watchlist.enabled` 時のみ）。 |
| `paper_trade_exception_summary.json` | 種別ごとの **例外カウント**と影響銘柄の記録（WARN 閾値の材料）。 |
| `same_day_replay_compare_summary.txt` | **同一日**の paper CSV と **fixed replay** 成果物を突き合わせた **差分サマリ**（手動 `--same-day-replay-compare` または **paper 日次 finalize** 時の best-effort 生成）。**突合条件は §7.4.5**。 |
| `paper_trade_seen_alerts.json` | **Discord Entry Embed** の **重複送信防止**用キー集合。 |
| `paper_trade_fetch_state.json` | **連続 fetch 失敗**カウント等の直近状態（WARN の二重鳴り抑制に利用）。 |
| `watch_symbols_snapshot.txt` | 起動時点の **監視銘柄一覧**と、紐づく **既定 paper 用 replay config 名・`config_hash`** のスナップショット。 |
| `paper_trade_seen_ids.json` | **CSV 行**レベルの **重複抑制**用 ID（poll 時刻粒度の `symbol|…` キー）。 |

#### 7.4.3.1 `notify_sent_count`（`paper_trade_summary.txt` / レポート `overall_summary`）

- **Discord の送信成功回数ではない**（API 成否は見ていない）。
- **`paper_trade_log.csv` の `signal_type=LIVE` 行のうち、`notify_sent` 列が `1` となった行数**（各 poll で `notify_row_n` として集計し、セッション累計 `total_notify_sent_count` に加算）。
- つまり **「Entry embed 送信用に `_paper_send_signal_embed` まで到達し、重複キー通過後に `notify_sent=1` を立てた行数」**（**signal notify 経路が発火した行**のカウンタ）。
- **補足:** **`_paper_send_signal_embed` が例外なく完了した後**に `notify_sent=1` が立つ。`discord_notify` が **HTTP 失敗を区別せず成功扱い**にする経路では、**Discord 上の到達件数と一致しない**ことがある。

**ロック（日付フォルダ外）:** `results/paper_trade/paper_trade.lock` — **paper_trade 専用**の二重起動防止（**`run_replay` にはロックをかけない**）。

- lock ファイルには **起動 PID** 等が JSON で保存される。
- 起動時、既存 lock があれば **記録 PID の生存を `_process_pid_is_alive` で確認**する。
- **PID が生存していない**場合は **stale lock** とみなし、**警告ログを出して上書き取得**する（`force` なしでも自動回収）。
- **`--paper-trade-force-start`:** 生存中の lock があっても **上書き**して起動する（運用注意）。

#### 7.4.4 runtime / health 管理（概要）

- **runtime metrics 集計:** 各 poll の `poll_duration_sec`、銘柄別 **fetch latency**、slow poll / slow fetch 件数などを **`paper_trade_runtime_metrics_summary`** として JSON サマリに載せ、`runtime_metrics` 行で LOG へ（有効時）。
- **fetch latency 監視:** 平均・p95・max に加え、**閾値 WARN**（例: 平均 ≥5s、連続 slow poll）を LOG へ。
- **exception summary:** 取得失敗等を型別に **`paper_trade_exception_summary.json`** に蓄積し、件数が段階を越えたとき **LOG に WARN**。各 poll でファイルから **再読込**し継続する（**累計カウントの永続先**）。
- **health report:** 日次 finalize で **`paper_trade_health_report.txt`** を書き、**生成通知1行**を ALERT（または quiet 組合せで LOG）。
- **`paper_trade_runtime_state.json`（restore）:** 日付が **`YYYYMMDD` の `state_day_key` と一致するファイルだけ**を復元対象とする（**別日の state は restore しない**）。一致しない、または欠損がある場合は **`[PAPER][WARN] runtime state restore skipped`** を出し、**累計カウンタ等はゼロから新規開始**。一致する場合は **`total_poll_count` / `total_fetch_count` / `total_fetch_failed_count` / `total_signal_generated_count` / `total_notify_sent_count` / `total_slow_poll_count` / `total_slow_fetch_count` / `ht_*`（セッション系累計）**、**`warn_cooldown_until`（WARN のクールダウン期限）**、**`last_exc_warn_floor`（例外 WARN のカウント床）**、**`consecutive_slow_poll`**、および **`runtime_started_at_jst` / `last_*_jst` / `last_market_state`** 等を復元する。**例外の型別カウント本体**は **`paper_trade_exception_summary.json`** 側で引き続き管理（runtime state は **床とクールダウン**中心）。
- **lock file:** **`paper_trade.lock`**（§7.4.3 本文）。**`--paper-trade-force-start`** は生存 lock も含め上書き可。

#### 7.4.4.1 `runtime_status`（`paper_trade_health_report.txt`）

`_paper_trade_compute_runtime_health_status` が **ヒューリスティック**で **`OK` / `DEGRADED` / `UNSTABLE`** を返し、**`health_reasoning`** として短文理由が列挙される。**閾値の厳密値は実装に従い変更され得る**ため、本書では概要のみ示す。

| 値 | 概要（実装の意図） |
|----|---------------------|
| **OK** | fetch 失敗率・slow poll / slow fetch・例外合計が **比較的軽い**帯（実装では「許容」と判断したときの既定メッセージ付き）。 |
| **DEGRADED** | fetch 失敗率や **poll / fetch の平均レイテンシ**が **やや悪い**、**例外がやや多い**、slow系が多め、等。加えて **`total_poll_count` が十分大きいのに `crossed_true` と `total_signal_generated_count` がゼロ**のとき **「signal pipeline で生成未観測」系の理由**が付く（**市場要因の可能性**の注釈。実装上は **DEGRADED** に分類される）。 |
| **UNSTABLE** | **fetch 失敗率が極めて高い**、**失敗件数が多く失敗率も高い**、**例外合計が極端に多い**、等の **最悪帯**。 |

**実装との差異メモ:** 「signal pipeline 停止疑い」を **UNSTABLE** と読みたい場合でも、**現行コードは上記ゼロ観測を `DEGRADED` 理由**として扱う（**§7.4.4.1 の表を正**）。

#### 7.4.5 Replay との違い（重要）・`same_day_replay_compare`

| 観点 | paper_trade | Replay（`--replay`） |
|------|-------------|---------------------|
| 実行関数 | **`run_paper_trade`** | **`run_replay`** |
| データ源 | **Yahoo の live snapshot**（`fetch_latest_intraday_data_for_paper_trade` 等） | 主に **`data/intraday_1m/` の CSV** を順再生 |
| VWAP | **fetch ベースの intraday VWAP**（スナップショット） | **セッション累積の再生 VWAP** と一致しない場合がある |
| `signal_generated_count` | **live の crossed 診断**に基づく（`paper_trade_screening_note` 参照） | Replay 集計の件数と **一致保証なし** |
| キャッシュ | **Replay 用 1分足キャッシュに依存しない**（ループ内で `run_replay` を呼ばない） | キャッシュ／取得ウィンドウに依存 |

**`same_day_replay_compare` の用途:** 指定日の **`paper_trade_log.csv`（LIVE 行）** と、**同一カレンダ日の fixed replay** 成果物（`*_signals.csv` / summary）を突き合わせ、**件数・マッチ・paper_only / replay_only** を **`same_day_replay_compare_summary.txt`** にまとめる。**replay 側ファイルが無い場合は生成スキップ**（`None`）。

**突合（マッチ）条件（実装 `_write_same_day_replay_compare_summary`）**

1. **銘柄（`symbol`）一致**
2. **エントリー時刻**が **±180 秒以内**（paper 側は CSV の `datetime_jst`、replay 側は `signals.csv` の **`entry_time_utc` を JST に変換**した時刻）
3. **エントリー価格**が **`round(価格, 1)` が等しい**（`_entry_price_match`：小数第1位まで一致判定）

**補足（ズレの前提）**

- **paper_trade** は **live Yahoo snapshot**、**replay** は **1分足順再生・セッション累積 VWAP** ベースのため、**完全一致は保証されない**。
- 本レポートの目的は **ズレの傾向把握**（原因タグ `VWAP_DIFF` / `POLL_TIMING_MISS` 等の集計）であり、売買ロジックの変更ではない。

**continuation-v1 の経路別整理**（executor・legacy 混入・**PnL を直接比較してはならない**ケース）は **§13.1／§13.1.1** を正とする（上表とは別軸）。

（サマリ本文の `【MATCHED】` 行も **「±3 分・小数第1位一致」** と説明されている。）

#### 7.4.6 `config_hash`・`replay_config_fingerprint`・`watch_symbols_snapshot`

- **`config_hash`:** 既定で読む **paper 用 replay config JSON**（現行は `configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json`）の **内容ハッシュ（SHA-256 hex）**。起動時 **`watch_symbols_snapshot.txt`** と **`paper_trade_summary.txt` / JSON レポート内 `paper_trade_runtime_settings`** に残す。
- **`replay_config_fingerprint (paper_trade):`** `paper_trade_summary.txt` 先頭付近の **見出しラベル**。その直下に **`config_name` / `config_path` / `config_hash` / `watch_symbols_snapshot_path`** を並べ、**「どの設定ファイルのどのバイト列で、どの監視集合だったか」**を後追い可能にする。
- **`watch_symbols_snapshot.txt`:** 実際に poll した **銘柄集合**のコピー（**`--watch` / `watchlist.json` / `symbols.csv` 解決後**）。Replay 側のレポートでも **`watch_symbols`** 等を残す経路があり、**検証の再現性**（設定＋ユニバース）を揃える。

**Replay サマリ側:** `run_replay` の合算レポートでは **`replay_settings` / `replay_config` に `config_hash`** を載せる経路があり、**sweep や再実行の差分比較**に使う。

#### 7.4.7 日次オペレーション例（運用の流れ）

1. **08:50** 前後 — `python yahoo_kabu_watch.py --paper-trade --paper-trade-interval 60` を起動（前日の **`paper_trade.lock` が残っていないか**確認。必要なら手動削除か **`--paper-trade-force-start`** は運用ポリシーに従う）。
2. **`PAPER_LOG`** — **startup self check** が想定通りか（`.env` / トークン set・`PAPER_*` set・`results` 書込可など）。
3. **場中** — **`PAPER_ALERT`** で **Entry Embed** の頻度・内容を監視（quiet モードならファイルのみ）。
4. **15:30 以降** — **EOD 行・health 通知**、および **`paper_trade_health_report.txt` / `paper_trade_runtime_state.json`** を確認。
5. **引け後** — 同一日付で **`--replay --replay-date YYYY-MM-DD`**（必要なら `--replay-config`）を実行し **fixed replay** 成果物を得る。
6. **`same_day_replay_compare_summary.txt`** — CLI `--same-day-replay-compare YYYY-MM-DD`、または paper プロセスの **finalize 連動生成**で差分を確認。
7. **health / runtime_metrics** — 遅延・fetch 失敗率・例外集計が閾値に達していないかを **TXT と LOG 履歴**で確認。

#### 7.4.8 仕様書メンテ上の注意（本範囲はドキュメントのみ）

本節は **運用仕様の記述**に限る。**以下はコード変更禁止**（ユーザー方針・リスク回避）: **売買ロジック**、**entry 条件**、**スコアリング**、**AUTO_BLOCK** 等の **挙動変更**。設計書の追記・修正のみ行うこと。

---

## 8. 依存関係・実行 {#8-依存関係実行}

- Python **3.10+** 想定、`requests` 必須。Issue Bot は `discord.py` 等（`requirements.txt` に従う）。**§8.1** の watchdog は **`psutil`** / **`tzdata`**（Windows で `zoneinfo` の IANA 名用。未導入時は watchdog 内で UTC+9 にフォールバック）を参照する。

**監視・データ・Replay（抜粋）**

```text
python yahoo_kabu_watch.py
python yahoo_kabu_watch.py --morning-screen
python yahoo_kabu_watch.py --replay --replay-config configs/replay_safe.json
python yahoo_kabu_watch.py --replay --replay-mode fast --replay-range random_apr --replay-repeat 10 --replay-seed 1
python yahoo_kabu_watch.py --replay --replay-range 60d --replay-date 2026-05-12 --replay-config configs/replay_safe.json
python yahoo_kabu_watch.py --replay --replay-range forward_split --replay-random-days 5 --forward-split-periods-path configs/forward_split_periods.json
python yahoo_kabu_watch.py --replay --replay-range forward_split --forward-split-validation
python yahoo_kabu_watch.py --paper-trade --paper-trade-interval 60
python yahoo_kabu_watch.py --paper-trade-dry-run-replay --paper-trade-dry-run-day 2026-05-12
python yahoo_kabu_watch.py --same-day-replay-compare 2026-05-12
python yahoo_kabu_watch.py --save-intraday-1m-eod
python yahoo_kabu_watch.py --intraday-1m-cache-report-only
```

**一括 sweep（§4.1 参照）**

```text
python yahoo_kabu_watch.py --vwap-distance-sweep
python yahoo_kabu_watch.py --daily-loss-stop-sweep
python yahoo_kabu_watch.py --regime-filter-sweep
python yahoo_kabu_watch.py --topix-weak-threshold-sweep
python yahoo_kabu_watch.py --signal-filter-sweep
python yahoo_kabu_watch.py --composite-filter-sweep
python yahoo_kabu_watch.py --regime-control-sweep
python yahoo_kabu_watch.py --weak-risk-filter-sweep
python yahoo_kabu_watch.py --strong-risk-filter-sweep
python yahoo_kabu_watch.py --strong-combo-filter-sweep
python yahoo_kabu_watch.py --strong-trend-quality-sweep
python yahoo_kabu_watch.py --strong-trend-quality-validation-sweep
python yahoo_kabu_watch.py --rising-lt50-validation-sweep
python yahoo_kabu_watch.py --rising-ratio-threshold-sweep
python yahoo_kabu_watch.py --weak-combo-filter-sweep
python yahoo_kabu_watch.py --auto-block-momentum-sweep
python yahoo_kabu_watch.py --strong-extension-threshold-sweep
python yahoo_kabu_watch.py --forward-risk-virtual-block-sweep
```

一括 sweep は内部で主に **`random_apr`**×**`n_repeat`** を用いるが、**`n_repeat` の決め方は sweep ごとに異なる**（下記）。再現性が必要なら **`--replay-seed`** を併用する。

- **`--vwap-distance-sweep` / `--daily-loss-stop-sweep` / `--topix-weak-threshold-sweep`:** `main` から **`n_repeat=10` 固定**（**`--replay-repeat` は参照されない**）。
- **`--forward-risk-virtual-block-sweep` / `--auto-block-momentum-sweep` / `--strong-extension-threshold-sweep`:** `sys.argv` に **`--replay-repeat`（または `--replay-repeat=`）が無いときだけ `n_repeat=10`**、コマンドラインで明示したときはその正の整数。
- **上記以外の sweep**（`--regime-filter-sweep` / `--signal-filter-sweep` 等）: `n_repeat = int(args.replay_repeat)` を渡す。`--replay-repeat` の **argparse 既定は 1** のため、**オプション省略時の実効回数は 1**（コード上 `if replay_repeat is not None else 10` の `else` は、既定値が常にセットされるため **実質未到達**）。

通常の **`--replay`**（`main` が `output_subdir = replay_<repeat_label>_<batch_stamp>` を組み立てて `run_replay` に渡す場合）では、成果物は **`results/YYYYMMDD/replay_<repeat_label>_<batch_stamp>/`** 配下にまとまる。`<repeat_label>` は `--replay-date` 時 **`fixed_<YYYY-MM-DD>`**、固定プール **`random_*`** 時はそのラベル、`forward_split` 時は `forward_split`、`--replay-random-days`>0 で非プール時は **`random_<N>d`**、それ以外は **`--replay-range` 文字列**。`YYYYMMDD` は `batch_stamp` の先頭8桁。コンソールの `results/replay_...` ログは日付フォルダ省略で出ることがある。**単一 run の `.txt` / `.json` の stem 規則**は **§12**。**`--forward-risk-virtual-block-sweep`** は `forward_risk_virtual_block_sweep_<時刻>/` に `sweep_summary.txt` と JSON を書く。

**通常 `--replay` のみ**（sweep 以外）のとき、`--replay-repeat` の **argparse 既定は 1**（CLI 省略時は 1 回）。

**Discord Issue Bot（別プロセス）**

```text
python discord_issue_bot/discord_issue_bot.py
```

### 8.1 Windows 自動復帰（watchdog + タスク スケジューラ） {#81-windows-自動復帰watchdog--タスク-スケジューラ}

**目的:** PC 再起動・Windows Update・Python プロセス異常終了後も、`discord_issue_bot` と `paper_trade` を無操作で立ち上げ直す。

| ファイル | 役割 |
|----------|------|
| `scripts/start_issue_bot.bat` | ルートへ正規化した `ROOT` で重複チェック（**`check_issue_bot_running.ps1`** が `Get-CimInstance -ClassName Win32_Process` を使用）。スキップ時は **PID と CommandLine** を `issue_bot_*.log` へ追記。起動時は **`start` → `cmd /c` で `run_issue_bot_inner.bat` のみ**。 |
| `scripts/run_issue_bot_inner.bat` | ルートで `cd` 後、`where python` / `python --version` / `CD` / 実行コマンドをログに出し、`python .\\discord_issue_bot\\discord_issue_bot.py` の **stdout/stderr を `logs/runtime/issue_bot_YYYYMMDD.log` へ追記**。 |
| `scripts/start_paper_trade.bat` | ルートへ `cd` し、既定の **`--paper-trade` + `--paper-trade-force-start` + `--replay-config configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json`** で `yahoo_kabu_watch.py` を起動。ログは **`logs/runtime/paper_trade_YYYYMMDD.log`**。既に `yahoo_kabu_watch.py` かつ `--paper-trade` を含むプロセスがあれば起動しない。 |
| `scripts/start_watchdog.bat` | **ランチャー:** `ROOT` を `pushd "%~dp0.."` で正規化し、**`logs/runtime/watchdog_launcher_YYYYMMDD.log`** に起動時刻・`CD_AT_LAUNCH` / `ROOT` / `whoami` / `where python` / `python --version` / **`PATH`** / 実行コマンド / `check_watchdog_running.ps1` の終了コード / `start` 直後の `errorlevel` を追記。**`check_watchdog_running.ps1`** で `watchdog.py` 実行中の二重起動を抑止し、問題なければ **`run_watchdog_inner.bat`** を `start` で起動。 |
| `scripts/run_watchdog_inner.bat` | **`pushd` でルート固定**後、**`where python` の先頭**で **`%ROOT%\scripts\watchdog.py`** を絶対パス実行（**`PYTHONUNBUFFERED=1`**）。stdout/stderr を **`logs/runtime/watchdog_YYYYMMDD.log`** へ追記。タスク スケジューラの cwd/PATH 差を吸収。 |
| `scripts/check_watchdog_running.ps1` | **`Get-CimInstance -ClassName Win32_Process -Filter "name='python.exe'"`** から **`CommandLine` に `watchdog.py` を含む**プロセスのみ検出。該当時はランチャーログへ PID/CommandLine を追記して `exit 0`（起動スキップ）。 |
| `scripts/watchdog.py` | **5 分間隔**で監視。起動時に **`os.getcwd()` / `sys.executable` / `__file__` / dotenv 絶対パス / 各 `start_*.bat` 絶対パス / JST タイムゾーン** を INFO ログ。`issue_bot` / `paper_trade` 復帰時は **restart attempt → bat spawn と child_pid → 4 秒後 alive チェック → `[WATCHDOG] restarted …`** をログ。`Asia/Tokyo` は **`tzdata` 未導入時は UTC+9 固定にフォールバック**（Windows 常駐向け）。 |

**タスク スケジューラ（推奨）:** タスク名例 **`tradebot_watchdog_start`**。トリガーは **「コンピューターの起動時」または「ログオン時」**。操作は **`scripts/start_watchdog.bat` の絶対パス**（「最上位のフォルダー」にリポジトリルートを指定）。これ 1 本で watchdog が常駐し、上記 2 プロセスの自動復帰を担う。

**注意:** watchdog は **売買ロジックに触れない**（起動・プロセス監視のみ）。`paper_trade` の CLI 内容は **`start_paper_trade.bat`** に固定されているため、運用コマンドを変える場合は bat のみを編集する。

---

## 9. 設計上の注意 {#9-設計上の注意}

1. **実時間の候補条件と Replay の候補条件は完全一致しない**（Replay は「出来高増加必須」「Entry 接近率」まで要求しないブロックがある）。検証結果と実アラートの差に注意すること。
2. **地合い `STRONG` / `NORMAL` / `WEAK` / `CRASH` は Replay 中心**であり、実時間メインループには組み込まれていない。
3. **非公式 API** 依存のため、欠損フィールド・レート制限への耐性が運用上重要。
4. ファイル先頭の **`WEAK_ENTRY_*` 等**は意図としての定数が残る一方、現行の `_quality_rejects` 経路では **WEAK 時は RSI/ATR/前場限定**などが中心である。
5. **`regime_filters` / `signal_filters` / `composite_signal_filters`** のキー名 `disable_*`（signal/regime 系）は直感と逆に感じることがある。**true = その条件でシグナルを除外（集計対象外）** という意味で読む（詳細は **§6.11・§6.12**）。`composite_signal_filters` 側は **`enabled: true`** が明示的ブロックのオンになる。
6. **`main()` のモード分岐は先勝ち**（§11.1）。例: `--intraday-1m-cache-report-only` と `--replay` を同時に付けても、**前者で即終了**し Replay は走らない。
7. **`--paper-trade` と `--replay` は同時指定不可**（エラー終了）。`--replay-disable-afternoon-entry` と `--replay-strict-afternoon-entry` も **同時指定不可**。
8. **後方互換 CLI:** 第1引数が `replay` のとき、`python yahoo_kabu_watch.py replay <range> <repeat> <mode> [config.json]` を **`--replay` 付きの argparse 形式**へ変換してから処理する（`main` 先頭）。
9. **ルートの `watchlist.json` と `discord_issue_bot/watchlist.json` は別ファイル**（§2）。Bot で追加した銘柄が監視に自動反映されない点に注意。
10. **`--paper-trade`（live）は `run_replay` を呼ばない**（`run_paper_trade`）。Replay の累積 VWAP や `signal_generated_count` と **一致保証はない**（**§7.4.5**）。**`--paper-trade-dry-run-replay`** は **内部で `run_replay` を1回呼ぶ**（単日キャッシュ再生・§13.6）。

## 10. 実装工程のトピック（これまでの経緯） {#10-実装工程のトピック}

本章は **「何のために」「どう実装し」「何が得られたか」** を、主要な実装トピックごとに整理したものである（時系列の厳密なコミットログではなく、設計書としての要約）。

### 10.1 リアルタイム監視と Discord 通知

| | 内容 |
|---|------|
| **目的** | Yahoo 非公式 API 由来のデータで銘柄を継続評価し、条件一致・突破・水準変化を運用者に伝える。 |
| **手段** | `yahoo_kabu_watch.py` のメインループ、`requests` によるクォート取得（v7 失敗時の chart フォールバック等）、`DISCORD_WEBHOOK_URL` による Embed 通知、候補・突破状態のスパム抑制ロジック。 |
| **結果** | 監視銘柄の「候補」「Entry 上抜け」「条件外れ」を Discord で追跡可能。**発注は行わない**（通知・判断支援）。 |

### 10.2 Replay・1分足キャッシュ・再現性

| | 内容 |
|---|------|
| **目的** | 同一ルールで過去の 1 分足を再生し、期待値・トレード統計を積み、実時間との差を意識したうえでルールを磨く。 |
| **手段** | `--replay` / `--replay-range`（**`forward_split`** 含む）/ **`--replay-date`**（単日 JST 固定・ランダム／`forward_split` の日付集合をバイパス）/ `--replay-config` / `--replay-repeat` / `--replay-seed` / **`--forward-split-validation`**、`data/intraday_1m/` の CSV キャッシュ、`--save-intraday-1m-eod`、`--intraday-1m-cache-report-only`。 |
| **結果** | オフラインでも日次・銘柄単位で再生可能。`results/YYYYMMDD/` 配下にサマリ・ランダムプール結果を蓄積し、以降の sweep・銘柄スコアの入力になる。 |

### 10.3 `entry_filters` と閾値 sweep（VWAP / RSI / ATR）

| | 内容 |
|---|------|
| **目的** | 「追い過ぎ」「過熱」「ボラ過大」など、エントリー直後の不利を減らす閾値をデータで選ぶ。 |
| **手段** | Replay 用 JSON の `entry_filters`（`vwap_distance_pct` / `rsi` / `atr_pct` 等）、`--vwap-distance-sweep` 等でグリッド実行し **`results/YYYYMMDD/`** 配下に比較表・セル出力を保存。 |
| **結果** | 朝枠＋ VWAP 乖離など **ベースラインに近い設定族**が定まり、sweep 結果を見ながら保守的〜攻めの帯を選べる。 |

### 10.4 地合い・朝のレジーム（`market_regime` / `regime_filters`）

| | 内容 |
|---|------|
| **目的** | TOPIX・上昇銘柄割合・朝の弱さ等で「戦わない局面」を Replay 上で除外し、全体 expectancy を安定させる。 |
| **手段** | `market_regime` 判定（TOPIX 代用 ETF・上昇比率等）、Replay config の `regime_filters`（`disable_morning_weak` / `disable_rising_ratio_lt50` / `disable_topix_weak` / `topix_weak_threshold_pct`）、`_apply_replay_config_to_flags`、`--regime-filter-sweep` / `--topix-weak-threshold-sweep` / `--rising-ratio-threshold-sweep` 等。 |
| **結果** | 地合い・朝弱・TOPIX_WEAK 帯を **ON/OFF や閾値で比較**でき、`configs/regime_filter_sweep/` と **`results/YYYYMMDD/regime_filter_sweep_*`** 等で判断材料が揃う。 |

### 10.5 シグナル前段のフィルタ（`signal_filters` と品質ゲート）

| | 内容 |
|---|------|
| **目的** | ギャップ過大・VWAP 乖離過大・時間帯以降のエントリー、および RSI/ATR/TOPIX 相対弱さ等で「事故りやすい一本足」を Replay 集計から外す（実時間では別経路の品質指摘も併用）。 |
| **手段** | `signal_filters`（`disable_gap_ge_pct` / `disable_vwap_distance_ge_pct` / `disable_entry_after_hhmm` 等）、定数ベースのシグナル品質（`SIGNAL_FILTER_*` / `WEAK_SIGNAL_FILTER_*`）、`--signal-filter-sweep`。 |
| **結果** | gap / VWAP 距離 / 時間帯 の **AB 比較**が可能になり、単独 feature に加えて運用方針と数値の対応が取りやすい。 |

### 10.6 当日損失上限・早期撤退・1 日 1 トレード等のリスク制御

| | 内容 |
|---|------|
| **目的** | 連敗・単日ドローダウンを抑え、Replay 上で「止めたときにどうなるか」を見える化する。 |
| **手段** | Replay config の `risk_controls.daily_loss_stop`、`--replay-early-exit` 系、`--one-trade-per-symbol-per-day`、`--daily-loss-stop-sweep`。 |
| **結果** | 例: **DD30k（100 株換算）** 等の帯が sweep で比較され、expectancy とトレード数のトレードオフを数値で議論できる。 |

### 10.7 Replay サマリの forward 軸（extension / HU / 銘柄ロバスト）

| | 内容 |
|---|------|
| **目的** | 「何回目 entry」より **前回シグナルからの騰落（伸びすぎ追撃）・高値更新差分・地合い**の相互作用で、forward で壊れにくいかを見る。 |
| **手段** | `extension_sweep_analysis`（`price_change_pct_from_prev_signal` の閾値ごとに「>= を仮想除外」）、`extension_hu_interaction_analysis`（`market_regime` × 騰落 bucket × `delta_high_update_count_before_entry` bucket）、`robustness_symbol_removal_analysis`（期待値上位銘柄を除外したときの expectancy / PnL / 連敗ドローダウン）。合算 TXT では `replay_summary_*_all_runs.txt` に **【EXTENSION_SWEEP_ANALYSIS】** 等として追記。 |
| **結果** | JSON の `report` 直下（および run 合算 dict）から同一キーで参照可能。**`symbol_daily_entry_index` は同日約定順の代理＝疲労の補助指標**であり、主判定・AUTO_BLOCK の第一候補からは外す。**`auto_block_strong_chase_after_extension_enabled` / `auto_block_strong_extension_hu_plus1_enabled` は既定 OFF**（分析で効果を確認してから）。 |

### 10.8 銘柄スコア・ブラックリスト・品質ブロック

| | 内容 |
|---|------|
| **目的** | Replay 実績の悪い銘柄を実時間の候補から外し、監視リソースと通知ノイズを減らす。 |
| **手段** | Replay 終了時の集計で `results/symbol_scores_latest.json` を更新、`_load_symbol_scoring_latest` で `blacklist_symbols` / `quality_blocked_symbols` を参照。 |
| **結果** | スコアファイルが存在する環境では、**候補ゲートに自動的に反映**され、継続的に「当たり外れ銘柄」を抑制しやすい。 |

### 10.9 複合条件（`composite_signal_filters`・combo sweep）

| | 内容 |
|---|------|
| **目的** | 単独 feature では見えにくい **「弱い組み合わせ」** を列挙・ブロック条件として扱い、事故パターンの削減に寄与する。 |
| **手段** | `composite_signal_filters`（例: `weak_combo_filter`）の正規化・一致判定、Replay メタへのスナップショット、各種 `--*-sweep` と **`results/YYYYMMDD/*_sweep_*/sweep_summary.txt`**。 |
| **結果** | 複数因子の組み合わせを **設定＋ sweep** で回せるようになり、`docs/TODO.md` でいう「事故る条件の組み合わせ」分析の足場になった（継続改善対象）。 |

### 10.10 ペーパートレード（仮想執行）

| | 内容 |
|---|------|
| **目的** | 実注文なしで、**live Yahoo** および **continuation-v1 の共有エンジン**上に検証ログを残し、Replay・dry-run と突合できるようにする。 |
| **手段** | **`--paper-trade`**（live: `run_paper_trade`）/ **`--paper-trade-dry-run-replay`**（オフライン: `run_replay` §13.6）、`--paper-trade-interval` / quiet 系、`--replay-config`（dry-run で未指定時は full_day 既定 JSON）、**`results/paper_trade/…`** と **`results/paper_trade_dry_run/…`**、**lock**。**§7.4**・**§13**。 |
| **結果** | **live は `run_replay` 非経由**だが、dry-run は **同一日のキャッシュ順再生**で gap を切り分けやすい。health・`same_day_replay_compare` 等で運用ギャップを追う。 |

---

## 11. コマンドライン引数・実行分岐（`yahoo_kabu_watch.py`） {#11-コマンドライン引数実行分岐yahoo_kabu_watch}

本章は **`parse_args` に登録された全オプション**と、**`main()` がどの順でモードを判定するか**をコードと一致させた一覧である（`yahoo_kabu_watch.py`）。

### 11.1 `main()` の分岐順序（先に一致した処理だけ実行して終了）

`parse_args` 直後の順序は次のとおり（**上から先に一致した分岐だけ**が実行され `return` する）。

0. **`--morning-screen`** → `run_morning_screen()` のみ実行して終了（**以降の項目には入らない**）。
1. **`--paper-trade-dry-run-replay`** … **`--paper-trade` または `--replay` と併用不可**（エラー終了）。**`--paper-trade-dry-run-day YYYY-MM-DD` 未指定でもエラー終了**。当日の `data/intraday_1m/<YYYY-MM-DD>/` に CSV が無い場合も終了。**`run_paper_trade_dry_run_replay` → `run_replay`**（単日 **`replay_date_fixed`**・`replay_mode=fast`・**`paper_trade_mode=True`**・`paper_trade_dry_run_artifacts_dir` 等）のみ実行して終了（詳細 **§13.6**）。
2. **`--paper-trade` と `--replay` の同時指定** → エラー終了。
3. （処理継続・早期 return なし）**`TEST_REPLAY_MODE`** = **`--replay` かつ `--paper-trade` でない**ときだけ **`True`**（dry-run はここより前で return 済み）。
4. **`forward_split` かつ `--replay-random-days` 未指定かつ `--replay-date` なし** → `replay_random_days` を **5000** に自動拡大（全日に近い母集団）。
5. **`--replay-config` 未指定** かつ Replay/paper いずれか → Replay は **`configs/replay_morning_vwap2.json`**（`_ensure_replay_configs_exist`）、paper は **`configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json`** を既定読込。（**`--replay-date` と `--replay-shadow-multi-day` の同時指定**はこの後段の検証で **エラー**）
6. **`--interval` ≤ 0** → エラー終了。
7. **`--intraday-1m-cache-report-only`** → キャッシュ coverage 表示のみで終了。
8. **`--save-intraday-1m-eod`** → EOD 1分足保存で終了。
9. **各種 `--*-sweep`** … **上から順に** `if` 一致したもの **1つだけ**実行して終了（複数 sweep フラグを同時に付けた場合、**先に書かれた分岐が勝つ**）。順序はコード上: `vwap-distance` → `daily-loss-stop` → `regime-filter` → `topix-weak-threshold` → `signal-filter` → `composite-filter` → `regime-control` → `weak-risk` → `strong-risk` → `strong-combo` → `strong-trend-quality` → `strong-trend-quality-validation` → `rising-lt50-validation` → `rising-ratio-threshold` → `weak-combo` → `auto-block-momentum` → `strong-extension-threshold` → `forward-risk-virtual-block`。
10. **`--same-day-replay-compare YYYY-MM-DD`** → `results/paper_trade/YYYYMMDD/same_day_replay_compare_summary.txt` のみ生成して終了。
11. **`--paper-trade`** → `run_paper_trade`（ロック `results/paper_trade/paper_trade.lock`）。
12. **`--replay`（`TEST_REPLAY_MODE`）** → `run_replay` ループ（`--replay-afternoon-compare` 時は同一 seed で通常／後場禁止／後場厳格化の **3連続**）。
13. **上記以外** → リアルタイム監視ループ。

### 11.2 互換ショートハンド

```text
python yahoo_kabu_watch.py replay <replay_range> <replay_repeat> <replay_mode> [replay_config.json]
```

は、内部で **`--replay --replay-range … --replay-repeat … --replay-mode … [--replay-config …]`** に変換される。

### 11.3 モード別 CLI 一覧（`argparse`）

**リアルタイム監視**

| オプション | 型・既定 | 概要 |
|------------|----------|------|
| `--interval` | float, **1.0** | ポーリング間隔（秒）。**> 0** 必須。 |
| `--watch` | str, 空 | カンマ区切り銘柄。`--watch-file` より **優先度低**。 |
| `--watch-file` | str, 空 | 1行1銘柄・`#` 行コメント可。最優先の固定リスト。 |
| `--print-all` | flag | 不一致銘柄も毎回ログ。 |
| `--only-changes` | flag | 候補リスト変化時のみ表示。 |

**Replay（`--replay`）**

| オプション | 型・既定 | 概要 |
|------------|----------|------|
| `--replay` | flag | 過去1分足を再生するテストモード（`TEST_REPLAY_MODE`）。 |
| `--replay-mode` | `normal` / `fast`, **normal** | `fast` は sleep 最小・集計優先。 |
| `--replay-fast-discord` | flag | fast でも Discord 通知 ON。 |
| `--replay-fast-verbose` | flag | fast でも進捗ログ多め。 |
| `--replay-fast-print-signal-details` | flag | fast でも終了時 signal 詳細をターミナルに。 |
| `--replay-market-debug` | flag | crossed 時に地合いデバッグ表示。 |
| `--replay-range` | choices, **1d** | `1d`〜`60d`、`random_*`、`forward_split`（§4 表参照）。ランダム系・`forward_split` は取得ウィンドウ **`60d`** にマップされる経路あり。 |
| `--replay-date` | str, 空 | **実装済み**。**単日 JST 固定**。ランダム／`forward_split` の日付集合をバイパス（`run_replay` 内で `replay_dates_jst` が当該日のみに固定）。**`--replay-shadow-multi-day` と併用不可**（衝突時は検証エラー）。 |
| `--replay-repeat` | int, **1** | 連続実行回数。 |
| `--replay-random-days` | int, **0** | ランダム抽出する営業日数。`0` で無効。 |
| `--replay-random-months` | int, **3** | ランダム母集団の月幅（`random_5d` 等のスライディング窓）。 |
| `--replay-seed` | int, 省略 | ランダム日抽出の再現用 seed。 |
| `--forward-split-validation` | flag | train 由来クラスタの validation/forward 再登場分析（AUTO_BLOCK 不変更）。 |
| `--forward-split-periods-path` | str, 空 | 既定 `configs/forward_split_periods.json`。 |
| `--replay-morning-screen` | str, 空 | Replay 内で **HH:MM（JST）** に朝スクリ相当を挿入。 |
| `--replay-early-exit` | flag | VWAP 割れ／直近5分安値割れの早期撤退を有効化（config とマージ）。 |
| `--replay-disable-afternoon-entry` | flag | 12:30 以降新規禁止。 |
| `--replay-strict-afternoon-entry` | flag | 後場エントリー厳格化（禁止ではなく絞り込み）。 |
| `--replay-afternoon-compare` | flag | 上記3パターンを **同一バッチ**で比較。 |
| `--replay-config` | str, 空 | Replay JSON パス。未指定時は上記 §11.1 の既定。 |
| `--replay-shadow-multi-day` | str, 空 | **`--replay` と併用**。カンマ区切り JST 日付を順に **`replay_date_fixed` 付き `run_replay`** し、`multi_day_shadow_summary.json` を出力（§13.14）。 |
| `--one-trade-per-symbol-per-day` | flag | 同一銘柄・同一 JST 日は 1 signal のみ採用。 |
| `--enable-add` / `--disable-add` | 排他 | ADD は **既定 OFF**。`--enable-add` で ON。 |

**paper_trade（live）および dry-run**

| オプション | 型・既定 | 概要 |
|------------|----------|------|
| `--paper-trade-dry-run-replay` | flag | **`--replay` / `--paper-trade` と排他**。`data/intraday_1m` の単日 CSV で **`run_replay` ベースの dry-run**（§13.6）。 |
| `--paper-trade-dry-run-day` | str, 空 | 上記用 **YYYY-MM-DD**。**必須**（未指定または形式不正で終了）。 |
| `--paper-trade` | flag | 実注文なしの仮想執行ループ（**`run_paper_trade`**・**`run_replay` は呼ばない**）。 |
| `--paper-trade-interval` | float, **60** | Yahoo ポーリング秒。**> 0**（実 sleep は `max(0.5, interval)`）。 |
| `--paper-trade-disable-discord-alerts` | flag | **§7.4.2** — `PAPER_ALERT` 向け（Entry Embed・EOD/health **通知1行**）を Discord で抑止。 |
| `--paper-trade-disable-discord-logs` | flag | **§7.4.2** — `PAPER_LOG` 向けテキストを Discord で抑止。 |
| `--paper-trade-force-start` | flag | **`results/paper_trade/paper_trade.lock`** を無視して起動（**§7.4.3**）。dry-run 経路では未使用。 |
| `--same-day-replay-compare` | str | 指定日の paper ログと fixed replay `*_signals.csv` を比較し **summary のみ**出力。 |

**データ・ユーティリティ**

| オプション | 型・既定 | 概要 |
|------------|----------|------|
| `--morning-screen` | flag | 寄り前スクリーニング（§3.2・§6.8）。 |
| `--save-intraday-1m-eod` | flag | 引け後想定で 1分足 CSV 保存（当日は **JST 15:30 前は拒否**、検証は `--force-intraday-1m-eod-time`）。 |
| `--intraday-1m-eod-date` | str, 空 | 対象日 YYYY-MM-DD。空は当日。 |
| `--force-intraday-1m-eod-time` | flag | 当日でも 15:30 前に保存可。 |
| `--intraday-1m-eod-delay-sec` | float, **0.15** | 銘柄間スリープ。 |
| `--intraday-1m-cache-report-only` | flag | ローカル CSV の coverage のみ。 |

**一括 sweep（各 flag は §4.1 の出力先表と対応）**

`--vwap-distance-sweep` / `--daily-loss-stop-sweep` / `--regime-filter-sweep` / `--topix-weak-threshold-sweep` / `--signal-filter-sweep` / `--composite-filter-sweep` / `--regime-control-sweep` / `--weak-risk-filter-sweep` / `--strong-risk-filter-sweep` / `--strong-combo-filter-sweep` / `--strong-trend-quality-sweep` / `--strong-trend-quality-validation-sweep` / `--rising-lt50-validation-sweep` / `--rising-ratio-threshold-sweep` / `--weak-combo-filter-sweep` / `--auto-block-momentum-sweep` / `--strong-extension-threshold-sweep` / `--forward-risk-virtual-block-sweep` — いずれも **store_true**。sweep 内の `run_replay` 呼び出しでは **`--replay-mode`** / **`--replay-seed`** / **`--replay-range`**（forward-risk 等は CLI 未指定時 `random_apr` を渡す経路あり）および **`n_repeat`**（§8 の表）を参照する。

---

## 12. 成果物の命名とディレクトリ規則 {#12-成果物の命名とディレクトリ規則}

### 12.1 Replay 出力ディレクトリ

- **`main` の `--replay` バッチ:** `output_subdir = "replay_" + <repeat_label> + "_" + <batch_stamp>` を `run_replay(..., replay_output_subdir=...)` に渡す。`_resolve_replay_results_dir` は非空 `replay_output_subdir` を **`results/YYYYMMDD/<subdir>/...`** にマップする（**CLI でこの文字列を直接指定するオプションは無い**）。
- **`<repeat_label>`（`main` 内）:** `--replay-date` 時は **`fixed_<日付>`**；`--replay-range` が `FIXED_REPLAY_RANDOM_POOLS` のキーならその文字列；`forward_split` なら `forward_split`；`--replay-random-days`>0 なら **`random_<N>d`**；else **`--replay-range` そのまま**。
- **`run_replay` 先頭の `replay_range_label`:** コンソール表示・ファイル名用。`--replay-date` 時は **`fixed_<日付>`**；ランダム正規化時は **`random_<N>d`** 等（`run_replay` 内ロジック）。ディレクトリ名の `<repeat_label>` は **`main` 側の `repeat_label`** と一致させている。
- **sweep:** 各 sweep が `output_subdir`（例: `vwap_sweep_<時刻>/thr015_random_apr`）を組み立て `run_replay` に渡す → **`results/YYYYMMDD/<output_subdir>/`**（`_build_results_dir_from_output_subdir`）。

### 12.2 単一 run のテキスト／JSON の接頭辞（`run_replay` 内）

`saved_at_jst = YYYYMMDD_HHMMSS`（JST）、`batch_stamp` はバッチ開始時刻で repeat 内共通。

| 条件 | ベース名 `name_base` |
|------|----------------------|
| `replay_repeat_total` > 1 | **`replay_summary_<replay_range_label>_<batch_stamp>_runXX`**（`run01` 形式） |
| `replay_repeat_total` == 1 かつ `replay_range_label` が **`random_` で始まる** | **`replay_summary_<replay_range_label>_<saved_at_jst>`** |
| `replay_repeat_total` == 1 かつ **`random_` でない**（`1d` / `forward_split` / **`fixed_…`** 等） | **`replay_<saved_at_jst>_range-<replay_range_label>`** → 例: `replay_20260512_224329_range-fixed_2026-05-12.txt`（`replay_range_label` が `fixed_2026-05-12` のため接続子は **`range-fixed_2026-05-12`**） |

同一 `name_base` で **`.txt` / `.json` / `*_signals.csv`** 等が揃う。

### 12.3 その他の定番ファイル

| パス | 用途 |
|------|------|
| `results/symbol_scores_latest.json` | Replay 終了時の銘柄スコア・ブラックリスト等。 |
| `results/paper_trade/paper_trade.lock` | **paper_trade 単一プロセス**用ロック（**§7.4.3**）。**`run_replay` には使用しない**。 |
| `results/paper_trade/YYYYMMDD/paper_trade_log.csv` | paper_trade **追記 CSV**（§7.4.3）。 |
| `results/paper_trade/YYYYMMDD/paper_trade_summary.txt` | poll ごとの **テキストサマリ**（`replay_config_fingerprint` ブロック含む）。 |
| `results/paper_trade/YYYYMMDD/paper_trade_health_report.txt` | **日次 health** 本文。 |
| `results/paper_trade/YYYYMMDD/paper_trade_runtime_state.json` | **runtime 永続化**（再起動時復元）。 |
| `results/paper_trade/YYYYMMDD/paper_trade_exception_summary.json` | **例外集計** JSON。 |
| `results/paper_trade/YYYYMMDD/same_day_replay_compare_summary.txt` | paper と **fixed replay** の突合結果（**§7.4.5**）。 |
| `results/paper_trade/YYYYMMDD/paper_trade_seen_alerts.json` | **Entry Embed 重複防止**キー。 |
| `results/paper_trade/YYYYMMDD/paper_trade_fetch_state.json` | **連続 fetch 失敗**など fetch 系 WARN 用状態。 |
| `results/paper_trade/YYYYMMDD/watch_symbols_snapshot.txt` | 起動時 **監視銘柄＋config フィンガープリント**（§7.4.6）。 |
| `results/paper_trade/YYYYMMDD/paper_trade_seen_ids.json` | **CSV 行**用の重複抑制 ID。 |
| `results/paper_trade_dry_run/YYYYMMDD/` | **`--paper-trade-dry-run-replay`** 時の **`paper_trade_dry_run_artifacts_dir`** 先。**`paper_trade_log.csv`** 等（§13.6）。 |
| `results/YYYYMMDD/paper_trade_dry_run_<YYYYMMDD>/` | dry-run **`run_replay` のバッチ出力**（`replay_output_subdir`）。通常の Replay ディレクトリ規則に従う。 |
| `results/vwap_sweep_summary_<時刻>.txt` | VWAP sweep 一覧（ルート互換）。 |

paper_trade（live）の Discord 振り分け・quiet モード・日次オペ例は **§7.4** を正とする。

---

## 13. continuation-v1: 共有エンジン・paper dry-run・Phase2 shadow {#13-continuation-v1-共有エンジンペーパードライランphase2-shadow}

**continuation-v1** 系のコードは **`yahoo_kabu_paper_trade_impl.py`** / **`yahoo_kabu_paper_trade_extended.py`** に集約され、`yahoo_kabu_watch.py` から参照される。

**読み方:** **§13.1** は **現実装**。**§13.2** は **目標アーキテクチャ**（達成したい状態）。理想とコードを混同しないこと。

### 13.1 Current implementation status（現実装の整理）

**`run_replay`（`--replay` の通常 Replay）は continuation-v1 と shared engine の整合を取りつつあり、現時点では **移行途中**である。`ReplaySignalEval` 系の **legacy 出口／集計経路が残る**ことはあり得る。

以下は **コード**（`run_replay` / `ReplaySignalEval.update_with_price` / `use_paper_position_exec`）に基づく。

| 経路 | continuation-v1 共有要素の位置づけ |
|------|--------------------------------------|
| **`run_paper_trade`（live `--paper-trade`）** | **shared signal / stop-take / exit（paper position exec）** が主系。`run_replay` は **呼ばない**。 |
| **`--paper-trade-dry-run-replay`** | 内部で **`run_replay(..., paper_trade_mode=True)`**。**paper_trade_mode** 側が主。 |
| **`--replay`（通常 `run_replay`）** | **`ReplaySignalEval` が集計の中心**。**`use_paper_position_exec` が true** のとき、各 signal に **`_paper_position_exec`** が付き **`update_with_price` が `_paper_trade_try_close_open_position` に委譲**。**false のとき** は **`ReplaySignalEval` 内の従来ロジック**が走る（例: **`exit_style=="fixed"` の固定利確**、**partial +1%**、**トレーリング** 等）。細部は **E1〜E7 メモ**（社内）と `ReplaySignalEval.update_with_price` を参照。 |

**最重要（誤用防止）:**

- **通常 replay の PnL／exit 統計を、continuation-v1 改善の評価にそのまま転用してはいけない**。run ごとに **`use_paper_position_exec`**・**`exit_style`**・ADD 有無を確認し、**legacy 混入 run では構造 take／共有出口の結論に使わない**。
- **`paper_trade` live／dry-run と `replay_fixed` の集計は「同一 executor 保証」ではない**。

#### 13.1.1 禁止比較（推奨）

| 禁止／要注意 | 理由 |
|--------------|------|
| **`replay_fixed` vs paper live の直接 PnL 比較** | データ源・出口経路・指標定義が異なる。 |
| **legacy replay exit と continuation 委譲 exit の混同** | 同一バッチでも **フラグ／`exit_style`** で切り替わる。 |
| **`fixed` +4% replay と structure_take の素朴な直接比較** | **実験設計**（同一 config・同一フラグでの A/B）が必要。 |

### 13.2 Target architecture（目標・理想状態）

データ取得のあと処理は **全 executor で一致させたい**理想像：

`market data` → **shared signal engine** → **shared stop/take engine** → **shared exit engine** → **executor のみ分岐**：

- replay executor  
- paper_trade live executor  
- paper_trade dry-run replay executor  

**許容される差分（目標）:** データ取得元、Discord 通知、lag シミュレーション、runtime の永続化、dry-run 用合成クォート、replay **レポート**の体裁。

**許容しない差分（目標）:** `entry_quality_score`／`stop`／`take`／`structure_take`／dynamic fallback／exit engine／`take_adjust` の**意図的な**食い違い——**※ legacy `ReplaySignalEval` が残る現状とは切り離して読む（§13.1）。**

### 13.3 Source of truth（情報の正）

| 項目 | source of truth |
|------|-----------------|
| **runtime 動作** | **実コード** |
| **architecture（全体および §13 の注意／禁止比較）** | **DESIGN.md** |
| **diagnostics／スキーマ／個別 run の数値** | **`replay_summary` / paper summary・各種 `.txt` / `.json` の実ファイル** |
| **社内メモ（例: E1〜E7）** | **補助資料**（**DESIGN.md・実コードと併読**。矛盾時は **コード優先**。メモ単体を SoT にしない）。 |
| **future roadmap** | **`docs/TODO.md`** |

### 13.4 `logic_version` と executor（誤読防止）

サマリの **`replay_logic_version: 2026-05-15-continuation-v1`** 等は **continuation-v1 系の設定ラベル**であり、単体では次を **保証しない**：(1) 全シグナルが shared exit のみであること、(2) legacy 分岐がゼロであること。

**必ず併視:** **`use_paper_position_exec`**、**`shared_signal_engine_version` / `shared_exit_engine_version`**（出力されている場合）、**出口理由・`exit_style` の分布**。

サマリ／trace に載せて正としたい項目例（キー名の詳細はコード）: `paper_trade_logic_version`、`replay_logic_version`、`dry_run_logic_version`、`shared_signal_engine_version`、`shared_exit_engine_version`、`use_paper_position_exec`。

### 13.5 機能別ステータス

| 領域 | status | 備考 |
|------|--------|------|
| paper_trade live の position exec／CSV | **implemented** | 主戦場 |
| dry-run：`paper_trade_mode` + 成果物 | **implemented** | §13.6 |
| Replay：`use_paper_position_exec` 委譲 | **partial** | false で legacy |
| Replay：集計の「shared のみ」化 | **partial** | **`ReplaySignalEval`** 残骸 |
| `paper_replay_divergence_report` | **implemented** | §13.15 |
| `multi_day_shadow_summary` | **implemented** | §13.14 |
| Phase2 shadow 列／テーブル | **partial** | config 依存 |
| `shared_engine_trace.jsonl` | **partial** | **拡張中** |
| 通常 `run_replay` の legacy 分岐撤去・**§13.2** への完全整合 | **planned** | 移行完了時は §13.1 表を更新 |

### 13.6 paper_trade dry-run replay

**CLI（実装済み・`main` では `--morning-screen` の直後に評価）:**

```text
python yahoo_kabu_watch.py --paper-trade-dry-run-replay --paper-trade-dry-run-day YYYY-MM-DD [--replay-config PATH]
```

**排他:** **`--paper-trade`** / **`--replay`** と **同時指定不可**。**`--paper-trade-dry-run-day`** は **必須**（空・形式不正・当日ディレクトリに CSV が1件も無い場合は終了コード 2）。

**実装概要:** **`run_paper_trade_dry_run_replay_impl`** が `data/intraday_1m/<YYYY-MM-DD>/` 内の **`*.csv` ファイル名から銘柄リスト**を構築し、**単日 `replay_date_fixed=<その日>`** で **`run_replay(..., replay_mode='fast', paper_trade_mode=True, paper_trade_dry_run_artifacts_dir=results/paper_trade_dry_run/<YYYYMMDD>/, replay_output_subdir=paper_trade_dry_run_<YYYYMMDD>, replay_batch_stamp=dry_run_<YYYYMMDD>)`** を一度呼ぶ。通常 Replay と同様、**詳細ログは `results/<バッチ日>/paper_trade_dry_run_<YYYYMMDD>/`** にもまとまる（`batch_stamp` / `replay_output_subdir` 規則は §12.1 と同系）。

**目的:** 市場時間外に **キャッシュ済み intraday CSV** で、live `run_paper_trade` に近い **paper_trade_mode 経路**を検証する。

**要件の要約:** fast・Discord はデフォルト抑止。**成果物:** **`results/paper_trade_dry_run/YYYYMMDD/`**（`paper_trade_dry_run_artifacts_dir` 指定）側の **`paper_trade_log.csv`・`paper_trade_summary.txt`・分岐レポート**等と、Replay 側 output subdir の JSON/CSV（**§13.15 divergence**）。

### 13.7 Entry quality（continuation-v1）

`entry_quality_score` は central score。構成要素・VWAP 早期崩れ診断 5 列・failure ペナルティ列はコード内の **`_PAPER_TRADE_*_CSV_FIELDNAMES`** に揃える。**まだ hard suppress には使わない**（`paper_entry_quality_min_for_open` 既定 **`None`/OFF**。shadow / diagnostics のみ）。

### 13.8 Stop / Take shared engine

replay / paper / dry-run は **`_paper_trade_compute_stop_take_for_signal(..., entry_quality_scores=...)`** を共有する。

**Take 選択の優先:** `structure_take` → `STRUCTURE_RELAXED`（proximity／中間 RR 等）→ `dynamic_rr_fallback` → 最終保険として legacy fixed（dynamic OFF 時）。診断用メタ: `take_structure_reason` に相当する `structure_take_*` / `nearest_resistance` / `take_selected_by` / `take_exit_kind` など。

既定 **`paper_structure_take_min_rr` は 0.55**（1.15 は検証上厳しすぎたため緩和。必要なら config で上げる）。

### 13.9 Structure take と診断

structure take 選択ゼロ・dynamic 過多・`VWAP_BREAK_EXIT` 偏重をログとカウンターで追う。**shadow に `structure_take_rr_relaxed_count`／`shadow_structure_relaxed_candidate_count`／仮想 PnL 系** を残し、multi-day で横断評価する。追加診断: `structure_take_distance_pct`・`structure_take_raw_rr`・`structure_take_after_epsilon_rr`・`structure_take_required_rr`・`structure_take_failed_rule`。

### 13.10 Shared exit engine（`use_paper_position_exec`）

**Replay** の **`ReplaySignalEval.update_with_price`** は、`use_paper_position_exec=true` のとき paper 側のクローズ経路（例: **`_paper_trade_try_close_open_position`**）へ委譲する設計とする。**共通出口ラベル:** `TAKE_HIT` / `STOP_HIT` / `VWAP_BREAK_EXIT` / `RECENT_5M_LOW_BREAK_EXIT` / `EARLY_WEAK_EXIT` / `TAKE_ADJUST` / `TIME_EXIT` / `MARKET_CLOSE_EXIT`（実装順と早期撤退の細部はコード参照）。

カウンター例: `opened_positions_count`〜`saved_profit_yen_100_shares`、`stale_execution_continued_count` など。VWAP break 平均系診断（`avg_vwap_break_*`）を summary に載せられるようにする。

### 13.11 Phase2 shadow diagnostics（本番ハードブロックは既定 OFF）

- **chase extension:** `replay_chase_extension_autoblock_enabled`（既定 false）、閾値 `replay_chase_extension_ge_pct`。shadow テーブル `shadow_chase_extension_autoblock_table`。有効化時ブロック理由 `CHASE_EXTENSION_BLOCK`。  
- **same symbol cooldown:** `same_symbol_cooldown_sec`、`same_symbol_cooldown_shadow_only`（既定 true）。`shadow_same_symbol_cooldown_table`。shadow_only=false で `SAME_SYMBOL_COOLDOWN_BLOCK`。  
- **market weakness:** `shadow_market_weakness_block_table`（`_compute_market_context_scores` と CSV 由来の breadth / lt50 を結合）。

Config はトップレベルまたは **`paper_trade_phase2`** オブジェクト内の同名キーで渡す（**`_apply_replay_config_to_flags`** がフラット化）。

### 13.12 paper_trade CSV

**VWAP 早期崩れ 5 列**（`pre_entry_vwap_hold_bars` … `vwap_break_early_risk_score`）は **live / dry-run 共通で正規ヘッダ**とし、`DictWriterextrasaction='ignore'` で落とさない（**`_paper_trade_csv_header_extend_phase2`** が欠落列を復元）。

Phase2 列・structure / exit 列は §10 ユーザー票に列挙されたキーを正とする。

### 13.13 paper_trade summary

`logic_version`、`replay_config_fingerprint`、`paper_trade_pipeline_counters`、`paper_trade_phase2_shadow_summary`（`*_shadow_*_table` 3系）、`paper_replay_divergence`、`entry_quality`、`execution_summary`、`dynamic_rr_fallback_reason_counts`、`structure_reject_reason_counts`、`PAPER_TRADE_LATENCY` 等を出力する（**多くは §13.5 implemented**。将来キー・列の網羅は拡張可）。

### 13.14 Replay multi-day shadow validation

**CLI:**

`python yahoo_kabu_watch.py --replay --replay-shadow-multi-day 2026-05-13,2026-05-14,2026-05-15 --replay-config configs/...json`

実装では **各 JST 日を `replay_date_fixed` で `run_replay` に渡し**、`replay_shadow_collect` に **`_build_replay_shadow_filter_validation`** の結果を貯める。最後に **`multi_day_shadow_summary.json`**（`multi_day_shadow_summary` / `multi_day_chase_autoblock_summary` / `multi_day_cooldown_summary` / `multi_day_market_weakness_summary` と評価指標）。

### 13.15 Replay / Paper divergence report

dry-run 終了後に **`paper_replay_divergence_report.{json,txt}`** を必ず出力する（replay 側イベント 0 件でもファイルは出す）。

比較キー・CSV bool 正規化（文字列 **`"False"`** を Python の `bool("False")==True` 扱いにしないため **`_parse_bool_cell` / `_paper_trade_normalize_replay_signal_row`**）、時刻キー **`datetime_jst[:16]`** と replay **`entry_time_utc`→JST 分** は実装側のユーティリティに寄せる。

### 13.16 shared_engine_trace.jsonl（status: partial）

**§13.5 status: partial**。**1行1イベント**。`crossed_true` / `signal_appended` / `replay_signal_written` と symbol・`timestamp_jst`・`engine_mode`・entry/stop/take・exit_reason・entry_quality 関連・Phase2・structure メタ等を順次載せられるよう拡張中（詳細・キー一覧はコードを正とする）。

### 13.17 検証コマンド（最低限）

1. `python -m py_compile yahoo_kabu_watch.py`  
2. `python -m unittest tests.test_paper_trade_dry_run_replay tests.test_replay_shadow_validation -v`  
3. dry-run / multi-day（上記 CLI）  
4. 成果物パス確認: `results/paper_trade_dry_run/...`、`results/.../multi_day_shadow_summary.json`

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
| 2026-05-10 | §10「実装工程のトピック（これまでの経緯）」を追加（目的・手段・結果）。目次に §10 を追記。 |
| 2026-05-11 | **現行出力レイアウト**を反映：`results/YYYYMMDD/` 階層、§4.1 sweep 表の整理・`--forward-risk-virtual-block-sweep` 追加、§4.2 見出し化、§8 の Replay パス説明を更新。 |
| 2026-05-13 | 実装との差分修正: **`--replay-output-subdir` CLI は存在しない**（内部 `replay_output_subdir` のみ）。**`main` 分岐は `--morning-screen` が最優先**（§11.1）。**sweep の `n_repeat`**（VWAP 等 10 固定、`sys.argv` 判定で 10 の 3 種、その他は **`--replay-repeat` 既定 1**）を §4.1・§8・§11.3・§12.1 に正確化。Discord の **`GITHUB_*` 定数の所在**表記を修正。 |
| 2026-05-12 | **§11・§12** 追加（全 CLI・`main` 分岐順・成果物命名 `range-<replay_range_label>`）。§2（`SWEEP_REPLAY_RANGES`・二重 watchlist・tools/scripts）、§7 表形式化・Discord コマンド、§6.12 `signal_filters` / **§6.15 `risk_controls.daily_loss_stop`**、§9 排他・ショートハンド追記。**`--paper-trade-interval` を `parse_args` に追加**（従来は常に 60s フォールバック）。 |
| 2026-05-09 | **paper_trade 最終整合:** §7.4.1 を **PAPER_ALERT 3種限定**＋監視ループ側のみの embed 明示。**`notify_sent_count`（§7.4.3.1）**。**`--replay-date` 実装済み**の明記（§4・§11.3）。**`same_day_replay_compare` 突合条件**（±180s・`round(...,1)`）。**lock の stale / force / replay 非対象**。**runtime restore 条件**と **exception JSON との役割分担**。**`runtime_status`（§7.4.4.1）** と **pipeline 未観測=DEGRADED** の実装差異注記。 |
| 2026-05-09 | **§7.1.1 `.env`（プロジェクト直下のみ）**、**§7.4 paper_trade 運用**（Discord 2ch・quiet・成果物・runtime/health・lock・Replay 差分・`same_day_replay_compare`・フィンガープリント・日次オペ例・ドキュメントのみ変更の注意）、**`DISCORD_TOKEN` / `DISCORD_BOT_TOKEN` の推奨表記**を追記。**§10.10**・**§12.3** を現行実装に合わせて更新。 |
| 2026-05-09 | **§13 設計レビュー反映**: **現実装（§13.1）と目標アーキ（§13.2）の分離**、`run_replay` の **legacy `ReplaySignalEval` 残骸** と **replay_fixed／paper_live の統計を直接対比しない** 明記、**logic_version は完全 shared を保証しない**（§13.4）、**機能別 status**・**SoT 表**・**禁止比較（§13.1.1）**、小节 **13.7〜13.17** へ繰り下げ。dry-run は **§13.6**。 |
| 2026-05-16 | **§13 continuation-v1** ほか **`--paper-trade-dry-run-replay` を実装に整合**: §11.1（morning 直後に dry-run・`run_replay` 呼出し）、§11.3、`--replay-date`∧`--replay-shadow-multi-day` 排他、§13 の dry-run 節ほか更新、§12.3・§4・§7.4 冒頭・§8・§9・§10.10。continuation の「PDF 未更新」注記は **以降 `md_to_pdf` で再生成**すること。 |
