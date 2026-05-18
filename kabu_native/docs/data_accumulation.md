# Data Accumulation (Phase 42)

Phase 37–41 で **top quartile exposure gate** は有効だが、**combined trades &lt; 100**・**symbols coverage &lt; 70%**・**May 16+ データなし** により small paper pilot には未達。  
Phase 42 は **ロジック変更なし** — `kabu_native/data/intraday_1m` と `push_jsonl` の蓄積基盤のみ。

## なぜロジック改善ではなくデータか

| 観察 | 含意 |
|------|------|
| quality≥0.55 / gate PF は IS・April OOS で改善 | EXIT/ENTRY の再設計より **サンプル・銘柄・日付** がボトルネック |
| legacy `data/intraday_1m` は 2026-05-15 まで | **May 16+** が無いと `oos_may_late` は永続 `no_data` |
| `kabu_native/data/intraday_1m` が空 | replay 正の保存先に **自前 kabu 系データ** が必要 |
| push_jsonl 空 | 将来の **実市場 replay** の原材料が無い |

## パス（legacy と分離）

| パス | 用途 |
|------|------|
| `data/intraday_1m/`（ルート） | Yahoo 由来・**read-only 参照**（既存 Phase 38/40） |
| `kabu_native/data/intraday_1m/YYYY-MM-DD/{symbol}.csv` | **新規蓄積正** — replay 第 1 候補 |
| `kabu_native/data/push_jsonl/YYYY-MM-DD/{symbol}.jsonl` | PUSH 生ログ（append-only） |

CSV 列（`replay/intraday.py` 互換）: `timestamp_utc,open,high,low,close,volume`

## CSV と PUSH JSONL の違い

| 形式 | 内容 | 使い道 |
|------|------|--------|
| **PUSH JSONL** | 板更新の生イベント列 | 日中記録 → EOD で 1 分足集計 |
| **intraday CSV** | 1 分 OHLCV | logic_lab / Phase 40 OOS replay 入力 |

日中に `record_push_jsonl.py`、引け後（またはセッション後）に `save_intraday_eod.py --source push`。

REST のみ（`--allow-rest-snapshot`）は **1 分スナップショット** であり全日足ではない。

## May 16+ を蓄積する理由

Phase 41 の `oos_may_late` は **2026-05-16 以降の営業日** が on-disk にあるまで `no_data`。  
IS（5/1–15）と April OOS だけでは **gate 通過 trade が重複** しやすく、combined 100 件・coverage 70% に届きにくい。

## 日次運用

```bash
# 1) 場中: PUSH 記録（kabu ステーション起動・.env に KABU_API_PASSWORD）
python kabu_native/scripts/record_push_jsonl.py \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --duration-sec 23400

# 2) EOD: JSONL → 1 分足 CSV
python kabu_native/scripts/save_intraday_eod.py \
  --universe kabu_native/data/universe/universe_intraday_full.csv

# 3) チェック
python kabu_native/scripts/check_data_accumulation.py \
  --universe kabu_native/data/universe/universe_intraday_full.csv
```

出力:

- `kabu_native/results/reports/data_accumulation_status_YYYYMMDD.json`
- `kabu_native/results/reports/data_accumulation_status_YYYYMMDD.csv`
- EOD 後: `phase41_data_oos/data_availability_for_oos.json`（`--no-update-oos-availability` で省略可）

## OOS / pilot 再評価（Phase 41 → 40）

1. `kabu_native/data/intraday_1m` に新しい営業日が増えることを `check_data_accumulation.py` で確認  
2. 必要なら `run_phase41_data_oos.py --run-latest-replay --revalidate-phase40`  
3. `move_to_small_paper_candidate` ゲート（combined trades≥100, coverage≥70%, PF≥1.20 等）は **変更なし**

quality≥0.55・max_concurrent=3 は Phase 39 設定のまま。

## モジュール

| モジュール | 役割 |
|------------|------|
| `src/storage/push_recorder.py` | JSONL append |
| `src/storage/intraday_recorder.py` | PUSH→1m、検証、CSV 書込 |
| `src/storage/symbol_sources.py` | universe / morning_screen / CLI |
| `src/storage/data_accumulation_report.py` | 日次ステータスレポート |

## テスト（API なし）

```bash
python kabu_native/scripts/record_push_jsonl.py --dry-run --symbols 9984,7203 --max-messages 40
python kabu_native/scripts/save_intraday_eod.py --symbols 9984,7203 --source push
python kabu_native/scripts/check_data_accumulation.py --symbols 9984,7203
```

## 制約（Phase 42）

- 新 EXIT / 新 ENTRY / threshold 最適化 **禁止**
- quality gate 0.55、max_concurrent 3 **維持**
- legacy `data/intraday_1m` **破壊・上書きしない**
