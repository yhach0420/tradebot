# kabu_signal_v1 / kabu_exit_v1 リプレイ検証（Phase 5G）

## 方針: paper_trade ではなくリプレイ優先

| 用途 | 推奨手段 | 理由 |
|------|----------|------|
| パラメータ調整・EXIT 閾値チューニング | **`scripts/kabu_signal_replay.py`** | 過去 1 日を数秒で再走。市場待ち不要 |
| kabu ゲート・スコアの傾向確認 | 同上 + 合成/実 PUSH | `exit_reason` 別損益が即見える |
| Yahoo 戦略との損益比較 | リプレイ + `--yahoo-replay-signals-csv` | 同一日・別プロファイルの並べ替え |
| 本番挙動・Discord・約定遅延 | `paper_trade` / 監視 | **検証完了後**のみ |

**`KABU_SIGNAL_SHADOW=1` の paper_trade シャドウ**はライブ品質の観測用。  
戦略改善の主戦場は本リプレイに移す。

---

## モジュール

| パス | 役割 |
|------|------|
| `scripts/kabu_signal_replay.py` | CLI・成果物出力 |
| `src/kabu_signal_replay.py` | イベント再生・仮想 ENTRY/EXIT・集計 |
| `src/kabu_signal_engine.py` | kabu_signal_v1 |
| `src/kabu_exit_engine.py` | kabu_exit_v1 |

**接続しないもの:** Yahoo `paper_trade` ポジション、Discord、発注、kabu 実売買。

---

## 入力データ

### 1. Yahoo 1 分足 CSV（必須・価格の時間軸）

```
data/intraday_1m/YYYY-MM-DD/<symbol>.csv
```

列: `timestamp_utc`, `open`, `high`, `low`, `close`, `volume`

### 2. kabu PUSH JSONL（任意・実 PUSH リプレイ）

```
results/kabu_push_probe/YYYYMMDD/push_probe_<symbol>_<ex>_<stamp>.jsonl
```

`--push-jsonl` 指定時は **Yahoo 合成ではなく JSONL を時系列再生**する。

### 3. kabu REST スナップショット（任意・板補強）

```
results/kabu_api/YYYYMMDD/kabu_api_check_<code>_<ex>_<stamp>.json
```

`--api-check-json` または銘柄コード一致で自動探索。Sell1〜5 / Buy1〜5 を合成イベントにマージ。

### 4. 合成 kabu イベント（PUSH が無いとき）

`--synthetic-push-keep 0.0〜1.0` で Yahoo 各行から board 相当メッセージを生成。

| オプション | 既定 | 説明 |
|------------|------|------|
| `--synthetic-push-keep` | 1.0 | 1分足行のサンプリング率 |
| `--synthetic-events-per-minute` | 10 | 1分あたり合成イベント数（G8 密度用） |
| `--synthetic-spread-bps` | 8 | 擬似 bid/ask 幅 |
| `--replay-relaxed-gates` | off | 合成リプレイ時のみ G3/G4/G6/G7/G8 を緩和（**本番ゲートと別**） |

合成時の `HighPrice` は **当バー更新前のセッション高値**（kabu 板の「直前までの高値」近似）。

> **注意:** 合成 PUSH は **検証・感度分析専用**。  
> Phase 5A で示したとおり、実 kabu PUSH（疎・板スナップショット）とは **品質が別**。  
> 本番相当の判定には `kabu_push_probe` の実 JSONL を使う。  
> 厳格ゲートのまま `--replay-relaxed-gates` なしだと、ENTRY が 0 件になりやすい（仕様どおり）。

---

## 仮想トレードロジック

### ENTRY（kabu_signal_v1）

次をすべて満たすイベントで仮想エントリー（1 銘柄 1 ポジション）:

- `breakout_event == true`
- `signal_score >= --entry-score-min`（既定 **60** = `notify_breakout` 相当）
- `timing_ok`（`--no-require-timing-ok` で無効化可）
- Tier **A / B**（C は ENTRY なし）

### EXIT（kabu_exit_v1）

保有中は **各イベント**で `evaluate_kabu_exit_v1()`。  
最初に成立した理由で決済（§11.9 優先順）。

### EOD

シーケンス終端で未決済なら **終値相当**で `eod_close`。

---

## 使い方

```powershell
cd <project_root>

# 1 日・複数銘柄（合成 PUSH 100%）
python scripts/kabu_signal_replay.py --day 2026-05-15 --symbols 9984.T,1321.T,5803.T --tier B

# 単一 CSV
python scripts/kabu_signal_replay.py --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv --tier A

# PUSH 疎密シミュレーション（検証用）
python scripts/kabu_signal_replay.py --day 2026-05-15 --symbols 9984.T --synthetic-push-keep 0.35

# 実 PUSH JSONL + REST 板
python scripts/kabu_signal_replay.py `
  --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv `
  --push-jsonl results/kabu_push_probe/20260515/push_probe_9984_1_HHMMSS.jsonl `
  --api-check-json results/kabu_api/20260515/kabu_api_check_9984_1_HHMMSS.json `
  --tier B

# Yahoo リプレイ結果と比較
python scripts/kabu_signal_replay.py --day 2026-05-14 --tier B `
  --yahoo-replay-signals-csv "results/20260516/replay_1d_*/replay_*_signals.csv"
```

---

## 出力

既定: `results/kabu_signal_replay/YYYYMMDD/kabu_replay_<stamp>/`

| ファイル | 内容 |
|----------|------|
| `kabu_replay_trades.csv` | 1 トレード 1 行 |
| `kabu_replay_trades.json` | 同上 JSON |
| `kabu_replay_summary.json` | 集計 KPI |
| `kabu_replay_by_exit_reason.csv` | **exit_reason 別** 件数・勝率・平均損益 |
| `kabu_replay_run_meta.json` | 実行パラメータ・銘柄別メタ |
| `yahoo_replay_compare.json` | Yahoo signals CSV との比較（指定時） |

### トレード列

`entry_time`, `entry_price`, `exit_time`, `exit_price`, `pnl_pct`, `exit_reason`,  
`max_favorable_excursion_pct`, `max_adverse_excursion_pct`, `elapsed_min`,  
`signal_score_at_entry`, `data_source`

### 集計（summary.json）

- `trades`, `win_rate`, `avg_pnl_pct`, `median_pnl_pct`, `max_loss_pct`, `avg_loss_pct`
- `stop_exit_count`（`hard_stop`）
- `breakout_failure_exit_count`
- `time_stop_count`
- `vwap_exit_count`（`vwap_reclaim_failure`）
- `by_exit_reason[]` — reason 別の詳細損益

---

## Yahoo リプレイとの比較

`--yahoo-replay-signals-csv` に `yahoo_kabu_watch` リプレイの  
`*_signals.csv`（`position_closed=true` かつ `excluded_from_eval=false`）を渡す。

`yahoo_replay_compare.json` 例:

- `yahoo_closed_trades` / `kabu_virtual_trades`
- `yahoo_win_rate` / `kabu_win_rate`
- `yahoo_avg_pnl_pct` / `kabu_avg_pnl_pct`
- `symbols_yahoo_only` / `symbols_kabu_only`

**プロファイルが異なる**ため数値一致は期待しない。  
同一日の **損益分布・トレード数・銘柄カバレッジ**の比較が目的。

---

## 構造分析（Phase 5I）

市場条件クラスタ・時間帯・ENTRY/EXIT 分離: [kabu_signal_structure_analysis.md](kabu_signal_structure_analysis.md)

```powershell
python scripts/kabu_signal_structure_analysis.py --day 2026-05-15 --tier B
```

---

## パラメータ横比較（Phase 5H）

閾値のスイープは [kabu_signal_param_sweep.md](kabu_signal_param_sweep.md) を参照。

```powershell
python scripts/kabu_signal_param_sweep.py --day 2026-05-15 --symbols 9984.T --tier B
```

---

## 関連ドキュメント

- [kabu_signal_param_sweep.md](kabu_signal_param_sweep.md) — 閾値スイープ
- [kabu_signal_design.md](kabu_signal_design.md) — ENTRY/EXIT 仕様
- [kabu_signal_validation.md](kabu_signal_validation.md) — プローブ・ライブシャドウ
- [kabu_bar_quality.md](kabu_bar_quality.md) — 合成 PUSH の位置づけ
