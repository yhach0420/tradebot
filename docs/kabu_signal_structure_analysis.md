# kabu_signal_v1 / kabu_exit_v1 構造分析（Phase 5I）

## 目的

**個別銘柄チューニング（9984 専用調整など）は禁止。**  
横断面の市場条件クラスタ・時間帯で「どこで壊れやすいか」を分析し、Tier / 流動性フィルタの妥当性を判断する。

| 問い | 分析の切り口 |
|------|----------------|
| どの市場条件で壊れるか | クラスタ × 時間帯 × EXIT 理由 |
| breakout_failure が厳しすぎか | BF 時の spread / push / 経過時間の分布 |
| ENTRY が悪いか EXIT が悪いか | 直後逆行率・MFE 率 vs BF 率 |
| ロジックかイベント品質か | `strict_timing_ok` 通過 vs `low_quality_entry` |

---

## 方針

- **リプレイ優先** — [kabu_signal_replay.md](kabu_signal_replay.md) と同じ合成 PUSH（検証用）+ 既定 `replay_relaxed_gates`
- **全銘柄同一 tier**（例: Tier B）— 銘柄ごとの閾値変更なし
- **クラスタは当日横断面のみ** — ドル出来高の三分位 + 構造 ETF + 値嵩しきい値

---

## 銘柄クラスタ（固定ルール）

| クラスタ | 付与条件 |
|----------|----------|
| `etf` | `1306.T`, `1321.T`（TOPIX/日経 ETF・地合い代用） |
| `high_price` | 当日 **median_close ≥ 10,000 円**（値嵩） |
| `ultra_high_liquidity` | 非 ETF で **median(dollar_volume) が上位三分位** |
| `low_liquidity` | 非 ETF で **下位三分位** |
| `mid_liquidity` | その他 |

優先順: **ETF → 値嵩 → 流動性三分位**。9984 は「超高流動クラスタの一員」として扱い、専用パラメータは持たない。

---

## 時間帯（JST）

| バケット | 時刻（JST） |
|----------|-------------|
| `opening` | 9:00–9:30 寄り直後 |
| `morning_mid` | 9:30–11:00 前場中盤 |
| `afternoon_open` | 12:30–13:00 後場寄り |
| `pre_close` | 14:30–15:00 引け前 |
| `other_session` | 上記以外の前場・後場 |

---

## ENTRY / EXIT 品質の分離

### ENTRY 品質（トレード成立後）

| 指標 | 定義 |
|------|------|
| `immediate_adverse_2m_rate` | エントリー後 2 分以内に含み損 ≤ **-0.15%** |
| `mfe_above_threshold_rate` | MFE ≥ **+0.25%**（伸びたか） |

### EXIT 品質

| 指標 | 定義 |
|------|------|
| `breakout_failure_share` | `exit_reason=breakout_failure` の比率 |
| `hard_stop_share` / `time_stop_share` / `eod_close_share` | 各 EXIT 理由の比率 |

### ロジック vs 低品質イベント

| ラベル | 定義 |
|--------|------|
| `low_quality_entry` | **本番相当** `KabuSignalV1Config` で `timing_ok=false`、または spread>15bps、push<8/min、主要 reject あり |
| `logic_stress_bf` | 低品質でないのに **5 分以内 BF + 負け**（ロジックストレス候補） |

**読み方**

- `low_quality_entry` の BF 率・逆行率が高い → **フィルタ不足（イベント品質）**
- `strict_gate_passed` で `logic_stress_bf_rate` が高い → **EXIT/ブレイク定義（ロジック）**の見直し候補
- 合成 PUSH では `low_quality_entry` が過大になりやすい → **実 PUSH でも再確認必須**

---

## breakout_failure 分布

BF 決済トレードについて、EXIT 時点の:

- `spread_bps`
- `push_density_1m`
- `trading_value`
- `volatility_5m_pct`（直近 5 分レンジ%）
- `time_since_breakout_min`（エントリーから BF までの分）

ヒストグラム付きで `breakout_failure_distribution.json` / `.csv` に出力。

---

## 使い方

```powershell
cd <project_root>

python scripts/kabu_signal_structure_analysis.py --day 2026-05-15 --tier B

# 本番ゲートに近い ENTRY 評価のみ厳格化（合成のまま）
python scripts/kabu_signal_structure_analysis.py --day 2026-05-15 --no-replay-relaxed-gates
```

---

## 出力

`results/kabu_signal_structure/YYYYMMDD/structure_<stamp>/`

| ファイル | 内容 |
|----------|------|
| `symbol_clusters.json` | クラスタ定義・銘柄一覧・当日 metrics |
| `trades_enriched.csv` | トレード + ENTRY/EXIT コンテキスト |
| `by_cluster_summary.json` | **クラスタ別** KPI |
| `by_time_bucket_summary.json` | **時間帯別** KPI |
| `breakout_failure_distribution.json` / `.csv` | BF 時の要因分布 |
| `entry_vs_exit_quality.json` | ENTRY vs EXIT 集計 |
| `logic_vs_event_quality.json` | 低品質 ENTRY vs 厳格通過 |
| `structure_analysis_summary.json` | 上記統合（trades 除く） |

---

## Tier / 流動性フィルタの判断

1. **`low_liquidity` クラスタ**で `low_quality_entry_rate`・BF 率が突出 → Tier C（ENTRY 禁止）または G8/G2 強化は **妥当**
2. **`etf` / `high_price`** は breakout 戦略の対象外または別プロファイルを検討（構造要因）
3. **`opening` / `afternoon_open`** で BF・逆行が集中 → 時間帯フィルタの根拠
4. **全クラスタで BF 100%** かつ合成 PUSH → まず **実 PUSH リプレイ**で再検証（[kabu_bar_quality.md](kabu_bar_quality.md)）

---

## 関連

- [kabu_signal_replay.md](kabu_signal_replay.md)
- [kabu_signal_param_sweep.md](kabu_signal_param_sweep.md)
- [kabu_signal_design.md](kabu_signal_design.md)
