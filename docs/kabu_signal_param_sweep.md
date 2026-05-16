# kabu_signal / kabu_exit パラメータスイープ（Phase 5H）

## 目的

[kabu_signal_replay](kabu_signal_replay.md) 上で **kabu_signal_v1 / kabu_exit_v1 の閾値を横比較**し、次のバランスを判断する。

- **大損削減** — `hard_stop` を浅くしても致命損が減るか
- **過剰早逃げ** — `breakout_failure` が厳しすぎて勝ち筋を切っていないか
- **時間切れ** — `time_stop` と `vwap_exit` のトレードオフ

**paper_trade は使わない。** 1 日分 CSV で数秒〜数十秒。

---

## ツール

| ファイル | 役割 |
|----------|------|
| `scripts/kabu_signal_param_sweep.py` | CLI・組合せ生成・成果物 |
| `src/kabu_signal_replay.py` | イベント再利用・`exit_config_from_sweep()` |

---

## スイープ軸（既定値一覧）

| パラメータ | 探索値 | リプレイ / EXIT への対応 |
|------------|--------|---------------------------|
| `entry_score_min` | 50, 60, 70, 80 | ENTRY: `signal_score >= 閾値` |
| `breakout_failure_minutes` | 1, 2, 3 | `fail_window_sec` = 分 × 60 |
| `breakout_failure_buffer_pct` | 0.05, 0.12, 0.20 | `fail_buffer_pct`（A/B 同一） |
| `hard_stop_pct` | -0.8, -1.0, -1.2, -1.35 | `hard_stop_pct_*`（**負値表記**、内部で絶対値） |
| `time_stop_min` | 5, 9, 12, 15 | `time_stop_max_*`（分） |
| `vwap_exit_buffer_pct` | -0.03, -0.05, -0.10 | `vwap_exit_below_pct_*` |

**baseline（`oaat` の固定側）** — Tier B 例: score 60, BF 2分/0.12%, HS -1.2%, TS 9分, VWAP -0.03%。  
Tier A は HS -1.35%, TS 12分, VWAP -0.05%。

---

## 比較指標

| 指標 | 説明 |
|------|------|
| `trades` | 仮想トレード数 |
| `win_rate` | 勝率 |
| `avg_pnl_pct` / `median_pnl_pct` | 1 トレードあたり損益 % |
| `total_pnl_pct` | 損益 % の単純合計（複利なし） |
| `max_loss_pct` | 最悪 1 トレード損益 % |
| `avg_loss_pct` | 負けトレード平均 % |
| `profit_factor` | 総利益 / \|総損失\| |
| `breakout_failure_exit_count` | ブレイク失敗 EXIT 件数 |
| `hard_stop_exit_count` | ハード STOP 件数 |
| `time_stop_exit_count` | 時間損切り件数 |
| `eod_close_count` | 引け/EOD 強制クローズ件数 |

---

## 使い方

```powershell
cd <project_root>

# 推奨: 1 軸ずつ比較（約 20 行 + baseline）
python scripts/kabu_signal_param_sweep.py --day 2026-05-15 --symbols 9984.T --tier B

# 複数銘柄
python scripts/kabu_signal_param_sweep.py --day 2026-05-15 --symbols 9984.T,1321.T,5803.T --tier B

# 全組合せ（最大 4×3×3×4×4×3 = 1728。--max-combos で打ち切り可）
python scripts/kabu_signal_param_sweep.py --day 2026-05-15 --symbols 9984.T --mode grid --max-combos 100
```

リプレイは [kabu_signal_replay](kabu_signal_replay.md) と同様、**合成 PUSH + `--replay-relaxed-gates`（既定 ON）** を使う。  
実 PUSH 検証は `kabu_signal_replay.py` の `--push-jsonl` を先に整備してからスイープに載せる（将来拡張）。

---

## 出力

`results/kabu_signal_param_sweep/YYYYMMDD/sweep_<stamp>/`

| ファイル | 内容 |
|----------|------|
| `sweep_results.csv` | 全組合せ 1 行 1 パラメータセット |
| `sweep_results.json` | 同上 + baseline メタ |
| `best_by_max_loss.csv` | `max_loss_pct` 降順（**-0.5 は -1.5 より良い**） |
| `best_by_total_pnl.csv` | `total_pnl_pct` 降順 |
| `best_by_profit_factor.csv` | `profit_factor` 降順 |

### 読み方の例

**breakout_failure が厳しすぎるか**

- `varied_param=breakout_failure_buffer_pct` または `breakout_failure_minutes` の行を比較
- `breakout_failure_exit_count` が trades の大部分 → 早逃げ過多の疑い
- 同時に `max_loss_pct` / `hard_stop_exit_count` が改善していなければ、閾値は緩める方向

**hard_stop を浅くしても損益が崩れないか**

- `varied_param=hard_stop_pct` で `-0.8` 〜 `-1.35` を比較
- `hard_stop_exit_count` 増加 + `max_loss_pct` 改善 → 大損削減に有効
- `total_pnl_pct` / `profit_factor` が大きく悪化 → 浅すぎ

---

## モード

| モード | 組合せ数 | 用途 |
|--------|----------|------|
| `oaat`（既定） | 各軸の値数 + baseline | **推奨**。原因パラメータの切り分け |
| `grid` | 最大 1728 | 相互作用の確認（`--max-combos` 推奨） |

---

## 関連

- [kabu_signal_replay.md](kabu_signal_replay.md) — リプレイ優先方針
- [kabu_signal_design.md](kabu_signal_design.md) §11 — EXIT 仕様
- [kabu_signal_validation.md](kabu_signal_validation.md)
