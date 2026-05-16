# kabu 生成 1 分足の品質比較（Phase 5A）

## 目的

- **Yahoo 1 分足**（`data/intraday_1m` キャッシュ）と **kabu PUSH 由来 1 分足**の差を定量化する。
- `signal_engine`（エントリータイミングゲート）が **kabu 足だけで実運用に耐えるか**を判断する材料を揃える。
- Yahoo 除去時に **どれだけ閾値・定義の再較正が必要か**を見える化する。

## ツール

| ファイル | 役割 |
|----------|------|
| `scripts/kabu_bar_compare.py` | 比較実行・JSON/詳細 CSV 出力 |
| `src/kabu_bar_builder.py` | JSONL → 1 分足（`MinuteBarBuilderFromPush`） |
| `src/signal_engine.py` | 同一ロジックで breakout / entry / score を両系列に適用 |

### 入力パターン

1. **`--yahoo-csv` + `--kabu-jsonl`** — 実機 `kabu_push_probe` の JSONL（推奨・本番に近い）
2. **`--yahoo-csv` + `--kabu-csv`** — 既にエクスポート済みの kabu 1 分足
3. **`--synthetic-push-keep FRAC`** — Yahoo 各行から擬似 PUSH を生成（**PUSH 疎密の感度分析用**。実 PUSH ではない）

```text
# 実 PUSH（例）
python scripts/kabu_bar_compare.py ^
  --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv ^
  --kabu-jsonl results/kabu_push_probe/20260516/push_probe_9984_1_*.jsonl

# 合成 PUSH（検証日一括・出来高 tier）
python scripts/kabu_bar_compare.py --batch-day 2026-05-15 ^
  --synthetic-push-keep-low 0.25 --synthetic-push-keep-high 0.75

# 単銘柄・中間疎密度
python scripts/kabu_bar_compare.py ^
  --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv --synthetic-push-keep 0.5
```

出力: `results/kabu_bar_compare/YYYYMMDD/kabu_bar_compare_<stamp>.json`（`--write-detail-csv` で分足マージ詳細も）

---

## 比較項目

### OHLCV（タイムスタンプ一致行のみ）

| 指標 | 内容 |
|------|------|
| `mean_diff` / `mean_abs_diff` | 平均差・平均絶対差 |
| `max_abs_diff` | 最大絶対差 |
| `median_abs_diff` / `p95_abs_diff` | 中央値・95 パーセンタイル |

対象: `open`, `high`, `low`, `close`, `volume`（および kabu 側に `vwap` 列がある場合）

### シグナル（`signal_engine`・両系列に `session_typical` VWAP）

| 指標 | 列・意味 |
|------|----------|
| **recent_5m_high** | `recent_5m_high_diff`（平均・最大絶対差） |
| **VWAP 乖離** | `vwap_distance_pct_mean_abs_diff` |
| **entry 候補** | `entry_candidate` が 1 円超で不一致した行数 |
| **breakout タイミング** | `breakout_cross_now` の XOR 行数 |
| **signal_score** | `signal_score` 不一致行数 |
| **entry timing（ゲート全体）** | `all_timing_gates_pass` の XOR 行数 |

breakout / entry は **状態機械**（`BreakoutStateTracker`）付きのため、価格が 1 円近くても **発火タイミングが 1 分ズレる**と mismatch になる。

### PUSH 疎密（kabu 側）

| 指標 | 意味 |
|------|------|
| `mean_push_samples_per_minute` | 1 分あたり PUSH 相当メッセージ数 |
| `minutes_with_zero_push` | サンプル 0 の分（合成では通常 0） |

---

## 実施結果（2026-05-15・全 26 銘柄・合成 PUSH）

**条件**

- 営業日: `2026-05-15`（`data/intraday_1m`）
- kabu 足: Yahoo から **擬似 PUSH**（`MinuteBarBuilderFromPush`）
  - 出来高 **下位 tier**（13 銘柄）: `keep=0.25`（約 25% の分のみメッセージ）
  - 出来高 **上位 tier**（13 銘柄）: `keep=0.75`
- 銘柄は当日 **累積出来高**で tier 分割（下位 13 / 上位 13）
- 集計 JSON: `results/kabu_bar_compare/20260516/kabu_bar_compare_20260516_192807.json`

> **注意**: 本結果は **実 kabu PUSH キャプチャではない**。実機 JSONL が取れたら `--kabu-jsonl` で同じ手順を再実行すること。

### tier 集計（OHLCV）

| 指標 | 低出来高 tier（keep=0.25） | 高出来高 tier（keep=0.75） |
|------|---------------------------|---------------------------|
| 平均 \|close 差\|（銘柄平均の平均） | **11.25 円** | **4.91 円** |
| Yahoo 分のうち inner 結合できた割合 | **約 24.7%** | **約 75.1%** |
| high の平均絶対差 | 0（合成で H/L を同分に載せたため） | 0 |

**解釈**

- PUSH が疎いほど **1 分バー自体が欠落**し、比較可能行が減る（低 tier は Yahoo 322 分のうち平均 ~80〜96 分しか揃わない）。
- close のズレは、**欠落ではなく残った分**でも合成サンプルが終値とずれることで増える（低 tier の方が大きい）。

### tier 集計（シグナル・全 eval 行ベース）

| 指標 | 低出来高 tier | 高出来高 tier |
|------|---------------|---------------|
| breakout タイミング不一致（行数合計） | 159 | 151 |
| entry 候補不一致（>1 円、行数合計） | 824 | 1124 |
| 全タイミングゲート不一致（行数合計） | 32 | 60 |

**解釈**

- breakout 不一致は **tier 合計では同程度**だが、銘柄あたりでは低 tier の方が **eval 行に対する比率が高い**傾向（例: 1321.T は Yahoo breakout 3 回 vs kabu 1 回）。
- `recent_5m_high` は疎な足で **窓の高値が過小**になりやすく、entry 候補の不一致が増える（例: 1321.T で entry 不一致 83 行 / recent_5m_high 平均絶対差 ~85 円）。

### 単銘柄参考（9984.T）

| 条件 | aligned 分 | close 平均絶対差 | breakout mismatch |
|------|------------|------------------|-------------------|
| keep=0.75（高 tier 既定） | 239 / 322 | 10.81 円 | 19 |
| keep=0.50（単体） | 167 / 322 | 11.83 円 | 18 |

流動性の高い 9984 でも、**合成 PUSH 25〜50% 欠落**に近いと close 差は 10 円前後、breakout 判定は **十数行単位でズレる**。

---

## PUSH 疎密の影響（要件 4）

| 要因 | 低出来高銘柄 | 高出来高銘柄 |
|------|--------------|--------------|
| 実市場での PUSH 頻度（想定） | 板更新が少ない | 更新が多い |
| 本検証での操作 | `keep=0.25` | `keep=0.75` |
| 観測 | バー欠落多・close 差大・entry/recent_5m_high ずれ大 | バー欠落少・close 差は相対的に小さい |

**結論（疎密）**: 出来高が少ない銘柄ほど、kabu 合成足は **カバレッジと OHLC 精度の両方**で不利。Yahoo 除去後は **銘柄別の最小 PUSH 密度**または **欠落分の補間方針**を決める必要がある。

---

## signal_engine の実運用可否（要件・完了条件）

| 観点 | 評価 | コメント |
|------|------|----------|
| **単体実行** | ○ | DataFrame / CSV のみで `eval_signals_on_ohlcv_dataframe` が動作（Phase 4C 済み） |
| **Yahoo 足との一致** | △ | 同一 `session_typical` VWAP でも、足がズレれば **ゲート・breakout が連鎖的に不一致** |
| **kabu 足のみでの監視** | △〜×（現状） | 実 PUSH JSONL 未収集のため本番差は未確定。合成では **breakout 十数〜数十行/日/銘柄**、**entry 数百行**規模のずれ |
| **実運用に必要な追加** | 必須 | ① 実 PUSH キャプチャでの再計測 ② kabu セッション VWAP 列の利用（`vwap_mode=column`）③ 銘柄別疎密しきい値 ④ 欠落分の扱い（スキップ vs 前値ホールド） |

**総合**: `signal_engine` の **ロジック分離は実運用可能な土台**になっている。一方、**入力 1 分足の品質が Yahoo 級でない限り、閾値をそのまま移植すると誤検知・見逃しが増える**。

---

## Yahoo 除去時の再較正ポイント（定量の目安）

1. **VWAP 定義** — バー近似 `session_typical` と kabu API `VWAP` は乖離する。ゲート `VWAP_DISTANCE_PCT=0.5` は **定義合わせ後に再チューニング**。
2. **recent_5m_high / entry** — PUSH 疎環境では 5 分高値が **数円〜数十円（銘柄による）**ずれる。`ENTRY_BREAKOUT_BUFFER` / `ENTRY_NEAR_RATIO` の再較正が必要。
3. **出来高ゲート** — kabu の `volume_delta` は累積出来高差分。ゼロ分が多いと「出来高増加なし」が増える。
4. **breakout 状態機械** — 1 分ズレで 🚀 相当イベントが別バーに移動。**許容ラグ（±1 分）**を評価指標に入れるか運用で決める。
5. **銘柄スクリーニング** — 低流動・PUSH 疎銘柄は **kabu 単独ウォッチから除外**するか、REST ポーリングで補完するかを分ける。

---

## 次のアクション

1. 取引時間中に `kabu_push_probe` で JSONL を取得し、本スクリプトを **`--kabu-jsonl`** で再実行する。
2. `signals_eval_probe.py --compare` と結果を突合し、タイミングゲート不一致の **時刻帯ヒートマップ**を見る。
3. kabu `VWAP` 列付き CSV で `kabu_bar_compare` + `vwap_mode=column`（kabu 側）を試し、VWAP 乖離差分を縮められるか確認する。

---

## 関連ドキュメント

- [kabu_yahoo_removal_feasibility.md](kabu_yahoo_removal_feasibility.md) — PUSH 仕様・除去可否
- [signals_eval_validation.md](signals_eval_validation.md) — `signal_engine` 単体検証
