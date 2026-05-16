# signals_eval 独立検証（Phase 4C）

## 目的

- **paper_trade / yahoo_kabu_watch を起動せず**、**1 分足（pandas DataFrame）だけ**で、監視ループと同じ「エントリータイミング」ゲートを再現・比較する。
- Yahoo キャッシュ CSV と、kabu PUSH から合成した 1 分足 CSV の **差がどこで増えるか**（再較正ポイント）を確認する。

## 実装場所

| ファイル | 役割 |
|----------|------|
| `src/signal_engine.py` | `calc_intraday_signals_from_series` / Entry 候補 / 拒否理由収集 / Breakout 状態機械 / DataFrame 走査 |
| `scripts/signals_eval_probe.py` | CSV 入力・単体実行・2 系列比較・CSV/JSON 出力 |
| `yahoo_kabu_watch.py` | 上記コアを `src.signal_engine` からインポート（単一ソース） |

## 含まれるゲート（本フェーズ）

`collect_watch_timing_reject_reasons` が再現するのは、**1 分足 + VWAP 由来のタイミング部分**に限る。

- **VWAP 乖離**（`VWAP_DISTANCE_PCT` 未満なら拒否）
- **直近 5 分高値ブレイク**（`recent_5m_high` 対比）
- **5 分前 close より上**（上昇傾向）
- **出来高増加**（直近 3 分合計 &gt; その前 3 分）
- **Entry 候補接近**（`entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER`、`ENTRY_NEAR_RATIO`）

**含まれないもの**: MA25、時価総額、地合い、朝スクリーニング、paper_trade 固有のシャドウ/品質ランクなどは **別レイヤ**。

## 出力列（主なもの）

評価 DataFrame の各行は「その 1 分足の終端 close を現値とみなしたとき」に相当する。

- `entry_candidate` — Entry 候補（`recent_5m_high * ENTRY_BREAKOUT_BUFFER`）
- `breakout_cross_now` — そのバーで entry を初めて上抜けしたか（状態機械）
- `recent_5m_high` / `price_5min_ago` / `vol_3m_gt_prev_3m`
- `vwap_used` — 評価に使った VWAP
- `vwap_distance_pct` — VWAP 乖離率(%)
- `reject_reasons` — タイミング拒否理由（`;` 区切り）
- `all_timing_gates_pass` — 拒否理由が空か
- `signal_score` — 全ゲート通過なら 1、否则 0（本番の「出来高 +1」相当を単純化）

## VWAP の扱い（重要）

本番の Yahoo 経路は **別 API のセッション VWAP** と **chart 1 分足** を組み合わせる。

プローブではソースが CSV のみなので、`eval_signals_on_ohlcv_dataframe` の `vwap_mode` で選択する。

- **`session_typical`**: 先頭〜当該行までの **(H+L+C)/3 × volume** のセッション累積 VWAP。**リプレイ比較用の単純近似**であり、API VWAP と一致しない。
- **`column`**: 列 `vwap`（名前変更可）をその行の VWAP として使う（**kabu が付与したセッション VWAP を並べた CSV**向け）。

**Yahoo 除去時の再較正ポイント**: 「どの VWAP 定義でゲートを揃えるか」をここで固定しないと、`vwap_distance_pct` とタイミング通過率が単純比較できない。

## 入力 CSV

- **OHLCV 列**: `open`, `high`, `low`, `close`, `volume` が必須
- **時刻**: 列 `timestamp` または `timestamp_utc`（`data/intraday_1m` の Yahoo キャッシュ形式）、または **DatetimeIndex**

## 使い方

依存: `pandas`（`requirements.txt` に追加済み）

```powershell
cd <project_root>
pip install -r requirements.txt
```

### 単一 CSV（例: Yahoo キャッシュ）

```text
python scripts/signals_eval_probe.py --csv data/intraday_1m/2026-05-15/9984.T.csv --label yahoo --vwap-mode session_typical
```

成果物（既定）:

- `results/signals_eval_probe/YYYYMMDD/signals_eval_yahoo_<stamp>.csv`
- `..._meta.json`

### Yahoo vs kabu 足の比較

同一銘柄・同一タイムスタンプ粒度を想定（タイムスタンプがズレると outer join で欠ける）。

```text
python scripts/signals_eval_probe.py --compare ^
  --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv ^
  --kabu-csv path/to/kabu_9984_1m.csv ^
  --yahoo-vwap session_typical ^
  --kabu-vwap column
```

成果物:

- `signals_eval_compare_<stamp>.csv` — `_yahoo` / `_kabu` サフィックス列 + `*_diff`, `timing_gate_mismatch`
- `signals_eval_compare_<stamp>.json` — 概要・不一致件数など

## 完了条件チェックリスト

| 要件 | 状態 |
|------|------|
| signals_eval が単独実行できる | `scripts/signals_eval_probe.py` |
| Yahoo 足と kabu 足を比較できる | `--compare` + merge |
| Entry / breakout / 5分高値 / VWAP乖離 / score を出力 | 上記列 |
| CSV/JSON 保存 | はい |

## Yahoo 除去に向けた読みどころ

- `timing_gate_mismatch` がどの時間帯に偏るか（午前クローズ直前、kabu が疎になる帯）。
- `recent_5m_high_diff` が大きい区間では、**ブレイク条件自体の定義**を変えずに Yahoo 完全一致は期待しない。
- `vwap_distance_pct_diff` が支配的なら、**まず VWAP の供給元（API vs バー近似）を揃える**のが第一歩。
