# JPX 銘柄マスタ設置手順（Phase 100）

動的ユニバース（`build_dynamic_universe.py`）の母集団は、**東証プライム / スタンダード / グロースの内国普通株**です。  
ETF・REIT・優先株等はマスタ生成時に一般ルールで除外します（銘柄固定の追加・除外はしません）。

## 1. JPX 公式ファイルの取得

1. ブラウザで [東証上場銘柄一覧（JPX）](https://www.jpx.co.jp/markets/statistics-equities/misc/01.html) を開く。
2. 直近月末の **「東証上場銘柄一覧（YYYY年MM月末）」** Excel をダウンロードする。
3. ファイルをリポジトリに保存する。

| 推奨保存先 | 説明 |
|------------|------|
| `data/jpx/raw/listed_issues.xlsx` | **推奨ファイル名**（スクリプトが最優先で参照） |

代替: 同ディレクトリに `.xls` / `.csv` でも可。複数ある場合は **非サンプルファイルのうち更新日時が最新** のものを使用します。

> **注意:** `jpx_listed_issues_sample.csv` はパーサ検証用の小さな fixture です。本番 shadow には **公式 Excel 由来の 500 銘柄超** の `tradable_symbols.csv` が必要です。

### Excel 形式（拡張子と実体）

JPX 配布は **OLE2（旧 `.xls`）** であることが多く、ファイル名が `listed_issues.xlsx` でも中身が xls の場合があります。  
ビルドは **マジックバイト** で判定し、`xlrd` で読み込みます。`openpyxl` で失敗した場合は LibreOffice headless で一時 xlsx に変換してから再読み込みします。

### 列名（2025 以降の東証上場銘柄一覧）

シート名 `Sheet1` を想定。必須列:

- `コード` → `symbol`（4桁数字はゼロ埋め、英字付きは例 `130A` → `130A.T`）
- `銘柄名` → `name`
- `市場・商品区分` → `market`（tradable は次の3区分のみ完全一致）
  - `プライム（内国株式）` → `prime`
  - `スタンダード（内国株式）` → `standard`
  - `グロース（内国株式）` → `growth`

任意: `33業種コード`, `33業種区分`, `規模区分`

UTF-8 に変換した CSV を置く場合も、列名が次を含むことを確認してください。

- `コード`（または `銘柄コード`）
- `銘柄名`
- `市場・商品区分`

## 2. マスタ CSV の生成

リポジトリルートで:

```bash
python kabu_native/scripts/build_jpx_symbol_master.py
```

任意で入力を明示:

```bash
python kabu_native/scripts/build_jpx_symbol_master.py --input data/jpx/raw/listed_issues.xlsx
```

検証のみ（Phase 100）:

```bash
python kabu_native/scripts/run_phase100_jpx_master_setup_check.py
```

## 3. 出力ファイル

| パス | 内容 |
|------|------|
| `data/jpx/all_symbols.csv` | 解析した全行 |
| `data/jpx/tradable_symbols.csv` | **動的ユニバースの既定入力** |
| `data/jpx/prime_symbols.csv` | プライムのみ |
| `data/jpx/standard_symbols.csv` | スタンダードのみ |
| `data/jpx/growth_symbols.csv` | グロースのみ |

### CSV 列

`symbol`, `exchange`, `market`, `name`, `sector_33_code`, `sector_33_name`, `scale_category`, `is_etf`, `is_reit`, `is_active`

- `exchange` は kabu ステーション用に **`1`**（東証）
- `market` は `prime` / `standard` / `growth` / `other`

## 4. 動的ユニバース（shadow）

マスタ配置後:

```bash
# 既定: board なしで static27 + dynamic23 → 50銘柄 CSV（Phase105）
python kabu_native/scripts/build_dynamic_universe.py --board-mode none

# 朝パイプライン（ビルド + 検証 + shadow コマンド出力）:
python kabu_native/scripts/run_phase106_shadow_live_pipeline.py

# 任意: 最終50銘柄のみ board 確認（置換なし・kabu 起動必須）
# python kabu_native/scripts/build_dynamic_universe.py --board-mode validate
```

shadow live（本番 pilot YAML は変更しない）:

```bash
python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live --full-session \
  --universe-csv kabu_native/results/reports/universe_dynamic_trial_YYYYMMDD.csv \
  --config kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml
```

## 5. トラブルシュート

| 症状 | 対処 |
|------|------|
| `need_user_to_download_jpx_file` | `listed_issues.xlsx` を `data/jpx/raw/` に配置 |
| `sample_master_only` | サンプル CSV のみ → 公式 Excel を配置して再実行 |
| `parser_fix_required` | 列名が JPX 形式と異なる → Excel を再ダウンロード、または CSV 列名をドキュメントに合わせる |
| `tradable_count` < 500 | 不完全な入力、または古いファイル |

## 6. 制約（変更しないもの）

- `kabu_native/configs/small_paper_pilot.yaml`（本番 pilot）
- `kabu_native/data/universe/universe_intraday_full.csv`
- entry / exit / quality / vol_liq / cap=3
