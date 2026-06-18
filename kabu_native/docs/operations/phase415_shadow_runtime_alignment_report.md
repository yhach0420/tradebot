# Phase415 — Shadow/Research Pipeline × Runtime Alignment Audit

目的:

- Phase414 で `same_symbol_open_policy=no_overlap_replace` を本番 paper YAML に入れる。
- この変更が **どの shadow/research pipeline に自動反映されるか**（= Runtime 生成 artifact を読むだけで追随するか）を監査する。

前提:

- `same_symbol_open_policy` は **Small Paper Runtime（`small_paper/pilot_runner.py`）の ENTRY 重複制御**。
- 研究モジュールが YAML を参照することは通常なく、**入力データ（structural_trades.csv 等）に反映されるか**が鍵。

---

## 結論（要約）

- **Phase409**: Runtime の `structural_trades.csv` を直接読むため、**6/17以降は自動反映される**。
- **Phase273 / Phase274**: `equity_curve_shadow.load_period_trades()` 経由で Runtime の `structural_trades.csv` を読むため、**6/17以降は自動反映される**。
- **Phase262**: `market_sector_heat.load_trades_by_day()` 経由で Runtime の `structural_trades.csv` を読むため、**6/17以降は自動反映される**。
- **Phase266**: この repo には該当する research/shadow パイプラインが見当たらない（コード上の `phase266` 実体なし）。

---

## 監査観点別チェック

### 1) Runtime structural_trades をそのまま読むか

- **Phase262**: YES  
  - `research/market_sector_heat.py::load_trades_by_day()` が `kabu_native/results/{small_paper|paper_trade}/**/structural_trades.csv` を走査して読む。
- **Phase273**: YES  
  - `research/equity_curve_shadow.py::load_period_trades()` → `market_sector_heat.load_trades_by_day()` を利用。
- **Phase274**: YES  
  - Phase273 同様に `load_period_trades()` を利用。
- **Phase409**: YES  
  - `research/phase409_boundary_forward_shadow.py::load_structural_trades_for_day()` が `results/small_paper/**/structural_trades.csv` を直接読む。
- **Phase266**: N/A（コード実体なし）

### 2) 独自 replay を持つか

- **Phase262**: NO（Runtime artifacts の集計 / forward shadow）
- **Phase273**: NO（Runtime artifacts の equity curve shadow）
- **Phase274**: NO（Runtime artifacts の equity curve shadow + band transition）
- **Phase409**: YES（ただし「Exit shadow replay」であり、Entry の挙動は入力 structural_trades に依存）
  - Phase409 自体は `prepare_corrected_trade_context()` / `simulate_corrected_boundary()` を使って exit shadow を計算する。
- **Phase266**: N/A

### 3) same_symbol_open_policy を参照するか

- **Phase262 / 273 / 274 / 409**: **直接は参照しない**（YAMLやconfigの同項目を読むコードなし）
  - 参照するのは Runtime が生成した structural_trades / summary / rejects などの **出力データ**。

### 4) 6/17以降の結果に自動反映されるか

- **Phase262 / 273 / 274 / 409**: **YES**
  - 理由: いずれも `structural_trades.csv` を読むため。
  - Phase414 により Runtime が `overlap_replaced_review` を抑止し reject を記録するなら、structural_trades の分布（trade_count/hold/exit_reason）が変化し、そのまま研究側入力が変わる。

### 5) 反映されない場合に乖離するか

今回の対象 4 つ（262/273/274/409）は structural_trades を読むため、**反映されないケースは「Runtime側が policy を適用していない」場合のみ**。

- policy が適用されない場合:
  - Runtime: 旧挙動（overlap replace chain 継続）
  - research: 旧 structural_trades を読み続けるため **乖離は発生しない**（両方とも旧挙動）

- policy が適用される場合:
  - Runtime: overlap replace 減、reject 増、hold 分布変化
  - research: structural_trades を読むので **自動追随**し乖離しない

---

## 明日（6/17）に確認すべき “alignment” 指標

- Runtime（paper session）:
  - `small_paper_summary.json`: `same_symbol_open_policy=no_overlap_replace`
  - `reject_reason_counts`: `REJECT_SAME_SYMBOL_OPEN_OVERLAP` 増加
  - `overlap_replaced_review_count` 減少
  - `small_paper_rejects.csv` に該当 reason が記録される
  - `structural_trades.csv` の trade_count 減少、hold 分布の変化

- Research/Shadow:
  - Phase409: `boundary_eligible_count` の変化（件数/率）
  - Phase273/274: `accepted_trade_count` / `rejected_trade_count` と equity curve の変化
  - Phase262: forward shadow entry rows の input 件数変化（元データが structural_trades のため）

