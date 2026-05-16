# kabu_native リプレイ（Phase 4）

**対象:** `kabu_native/src/replay/`、`kabu_native/scripts/run_replay.py`

## 方針

| 項目 | 内容 |
|------|------|
| **主検証** | **リプレイ**（`kabu_signal_v1` + `kabu_exit_v1`） |
| **非目的** | paper_trade 実運用の代替ではない |
| **エンジン** | ルート `src/kabu_signal_replay.py` 等を **参照のみ**（移動・削除しない） |
| **入力足** | 1分足 CSV → **合成 kabu board イベント**（旧 `push_messages_from_yahoo_df` と同系） |

実 kabu PUSH JSONL は将来 `data/push_jsonl/` からの入力に拡張可能。現 Phase 4 は **intraday CSV バッチ** が主経路。

## データの所在

### 読み込み順（`configs/replay.yaml` の `data_roots`）

1. `kabu_native/data/intraday_1m/YYYY-MM-DD/<symbol>.csv`（新系・今後の蓄積先）
2. `data/intraday_1m/YYYY-MM-DD/<symbol>.csv`（**旧系・read-only 参照**）

旧系キャッシュを壊さず、存在すればそのまま利用します。

### 3月データについて

- リポジトリに **2026-04 以降** の `data/intraday_1m` が中心（例: `2026-05-01`〜`2026-05-15`）。
- **2026-03（3月）のフォルダが無い日**は `skipped_inputs.csv` に `missing_intraday_csv` として記録されます。
- 3月を検証したい場合は、旧系 `yahoo_kabu_watch.py --save-intraday-1m-eod` 等で CSV を蓄積し、上記パスに置いてください。

### データ蓄積方針

| 用途 | 推奨パス |
|------|----------|
| 新規 EOD 保存（将来） | `kabu_native/data/intraday_1m/` |
| 既存 Yahoo キャッシュ | `data/intraday_1m/`（参照のみで可） |
| kabu PUSH 生ログ（将来） | `kabu_native/data/push_jsonl/` |

## 入力銘柄

| オプション | 説明 |
|------------|------|
| `--symbols 9984.T,8306.T` | 直接指定 |
| `--universe path/to/universe_YYYYMMDD.csv` | `passed=true` のみ（設定で変更可） |
| `--morning-screen path/to/dir_or.csv` | `pass_screen=true`（最新 CSV を自動選択可） |

いずれかと日付範囲が必須です。

## 実行例

```bash
python kabu_native/scripts/run_replay.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --symbols 9984.T,8306.T

python kabu_native/scripts/run_replay.py \
  --start-date 2026-05-12 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_20260516.csv

python kabu_native/scripts/run_replay.py \
  --start-date 2026-05-15 \
  --end-date 2026-05-15 \
  --morning-screen kabu_native/results/morning_screen/20260516/
```

## スキップ理由（`skipped_inputs.csv`）

| skip_reason | 意味 |
|-------------|------|
| `missing_intraday_csv` | 該当日・銘柄の CSV が無い |
| `empty_csv` | ファイルはあるが行が無い |
| `invalid_columns:...` | OHLCV 列不足・正規化失敗 |

**行は落とさず** スキップ一覧に残します。

## 出力

`kabu_native/results/replay/YYYYMMDD/replay_<stamp>/`

| ファイル | 内容 |
|----------|------|
| `trades.csv` | 仮想トレード一覧 |
| `daily_summary.csv` | 日別集計 |
| `symbol_summary.csv` | 銘柄別集計 |
| `aggregate_summary.json` | 全体 + 日別・銘柄別・exit_reason |
| `skipped_inputs.csv` | スキップした (日, 銘柄) |

### 集計指標

- `trades`, `win_rate`, `total_pnl_pct`, `avg_pnl_pct`, `median_pnl_pct`
- `max_loss_pct`, `avg_loss_pct`, `profit_factor`
- `exit_reason_counts`（全体・日別・銘柄別）

## 設定（`configs/replay.yaml`）

- `tier`, `entry_score_min`, `require_timing_ok` — シグナル閾値
- `relaxed_signal` — 合成 PUSH 向け緩和（検証用）
- `synthetic_*` — 1分足 → 合成 board の密度・スプレッド

## 旧系リプレイとの違い

| 項目 | 旧 `scripts/kabu_signal_replay.py` | kabu_native |
|------|-----------------------------------|-------------|
| 単位 | 主に単日 CLI | **日付範囲 × 複数銘柄** 一括 |
| 銘柄元 | 手動 `--symbols` | universe / morning_screen 連携 |
| 出力 | `results/kabu_signal_replay/` | `kabu_native/results/replay/` |
| スキップ記録 | 限定的 | `skipped_inputs.csv` 必須 |

## 関連

- [universe.md](universe.md)
- [morning_screen.md](morning_screen.md)
- ルート `docs/kabu_signal_replay.md`
