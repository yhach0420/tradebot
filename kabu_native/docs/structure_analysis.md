# kabu_native リプレイ構造分析

**更新:** Phase 7 — 全 intraday 在庫 27 銘柄リプレイ（`replay_20260516_221700`）

## 目的

- **9984 単体最適化を避ける** — 構造・クラスタ単位で `kabu_signal_v1` / `kabu_exit_v1` を評価
- 個別銘柄パッチは禁止。一般化できる弱点を特定する

## ワークフロー

```bash
# 1. 在庫確認
python kabu_native/scripts/audit_intraday_data.py

# 2. 全銘柄リプレイ（intraday 在庫 27 銘柄）
python kabu_native/scripts/run_replay.py \
  --start-date 2026-04-10 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv

# スクリーニング通過 3 銘柄のみ（比較用）
python kabu_native/scripts/run_replay.py \
  --start-date 2026-04-10 --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_20260516.csv

# 3. 構造分析（最新 replay ランのみ）
python kabu_native/scripts/analyze_replay_results.py
```

成果物:

- リプレイ: `kabu_native/results/replay/YYYYMMDD/replay_<stamp>/`
- 分析: `kabu_native/results/reports/structure_analysis_YYYYMMDD.{csv,json}`

## Phase 7 サマリ（27 銘柄・2026-04-10〜05-15）

| 項目 | Phase 6（2 銘柄） | Phase 7（27 銘柄） |
|------|-------------------|---------------------|
| trades | 20 | **83** |
| トレード発生銘柄 | 2 | **10 / 27** |
| total_pnl_pct | -10.57 | **-70.34** |
| 9984 損失シェア | ~99.8% | **~48%**（集中度フラグ解消） |
| BF 比率 | 90% | **76%** |
| `pnl_concentrated_in_one_symbol` | あり | **なし** |

**結論:** 9984 依存は **相対的に低下**したが、9984 は依然最多トレード（54）・最大損失（-33.7%）銘柄。**構造的問題が主**。

---

## 1. 全体成績

| 指標 | 値 |
|------|-----|
| trades | 83 |
| win_rate | 0% |
| total_pnl_pct | -70.34 |
| avg_pnl_pct | -0.85 |
| max_loss_pct | -4.94 |
| profit_factor | 0 |

---

## 2. 銘柄集中度

| 指標 | 値 | 解釈 |
|------|-----|------|
| largest_abs_share | **47.9%**（9984.T） | 50% 未満 → 単一銘柄支配フラグ **オフ** |
| 9984 trades | 54 / 83（65%） | 活動量は 9984 に偏る |
| 9984 total_pnl | -33.74 |  worst symbol |
| 2nd worst | 5803.T（-13.28, 15 trades） | 9984 以外でも損失 |

**トレードが発生した 10 銘柄:** 9984.T, 5803.T, 5016.T, 8001.T, 6890.T, 7013.T, 6315.T, 8002.T, 8058.T, 8306.T

**17 銘柄は trades=0**（シグナル未発火 or データスキップ）→ **trades 不足が構造分析の大きなノイズ**。

---

## 3. Tier 別（スコア proxy）

リプレイは engine tier **B 固定**。分析では `signal_score_at_entry` から proxy:

| tier_proxy | trades | total_pnl_pct |
|------------|--------|---------------|
| tier_proxy_A（≥80） | 67 | -54.26 |
| tier_proxy_B（60–79） | 16 | -16.08 |

→ 高スコア帯ほどトレード数は多いが、**どちらも全損**。tier 別パラメータより **EXIT 共通**を優先。

---

## 4. spread / 流動性別

universe メタ（`universe_20260516.csv`）の spread / TradingValue で三分位:

| spread_bucket | trades | total_pnl_pct |
|---------------|--------|---------------|
| high（狭い） | 55 | -33.76 |
| unknown（メタ無） | 28 | -36.58 |

| liquidity_bucket | trades | total_pnl_pct |
|------------------|--------|---------------|
| high（売買代金大） | 55 | -33.76 |
| unknown | 28 | -36.58 |

→ **流動性・スプレッドで改善しているわけではない**（高流動でもマイナス）。9984 は high バケットに含まれる。

---

## 5. breakout_failure 比率 — 全 universe 共通か？

| 指標 | 値 |
|------|-----|
| 全体 BF share | **75.9%**（63/83） |
| BF≥70% の銘柄 | 3/10（9984.T, 8001.T, 6315.T）= **30%** |
| `is_universal` | **false** |

| symbol | BF share |
|--------|----------|
| 9984.T | 90.7% |
| 6315.T | 100% |
| 8001.T | 100% |
| 5803.T | 53% |
| 8306.T | 0%（hard_stop 系のみ） |

**結論:** BF 偏重は **universe 全体の共通現象（76%）** だが、**全銘柄が 90% ではない**。調整対象は **kabu_exit_v1 の fail_window / fail_buffer（共通）**。9984 だけの EXIT は不要。

---

## 6. 時間帯別 — 寄り直後は全銘柄共通か？

| time_band (JST) | trades | total_pnl_pct |
|-----------------|--------|---------------|
| **09:00–09:30** | 27 | **-27.95** |
| 12:30–13:30 | 14 | -13.00 |
| 09:30–10:30 | 20 | -10.07 |
| 10:30–11:30 | 13 | -9.36 |
| 13:30–14:30 | 8 | -9.94 |

**寄り直後（opening）銘柄別:**

| symbol | opening trades | opening pnl |
|--------|----------------|-------------|
| 9984.T | 20 | -19.05 |
| 5803.T | 4 | -4.39 |
| 5016.T | 2 | -4.18 |
| 8058.T | 1 | -0.34 |

- opening トレードがある **4 銘柄はすべてマイナス**（`share_symbols_negative=100%`）
- ただし **10 銘柄中 6 銘柄は opening トレード自体なし**
- `is_universal_problem: true` は「opening がある銘柄に限れば全滅」

**結論:** 寄り直後が悪いのは **広く見て共通パターン**（全体の約40%の損失）だが、**全銘柄に opening ENTRY があるわけではない**。対策は **時間帯専用ロジックを新設する前に**、共通 ENTRY ゲート（例: 寄り後 N 分）を **全銘柄同一閾値**でスイープ検証するのが妥当。

---

## 7. PF 分布・trades 不足

**PF:** 全銘柄 PF=0（勝ちトレードなし）。`pf_by_symbol` は JSON 参照。

**trades 不足:**

| 項目 | 値 |
|------|-----|
| 期待銘柄 | 27 |
| トレードあり | 10 |
| ゼロトレード | **17** |
| skipped_inputs | 432（主に `missing_intraday_csv`・休場日） |
| 中央値 trades/銘柄 | 2 |

→ サンプル偏り大。**より多くの営業日・銘柄で ENTRY が発火する条件緩和**は、分析と別途、合成 PUSH 限界とセットで検討。

---

## 過学習チェック（Phase 7）

| フラグ | Phase 6 | Phase 7 |
|--------|---------|---------|
| pnl_concentrated_in_one_symbol | あり | **なし** |
| exit_reason_heavily_skewed | あり | **あり**（76%） |
| losses_driven_by_few_symbols | あり | **なし** |

---

## 構造的問題 vs 個別問題

| 観点 | 判定 |
|------|------|
| 9984 だけの問題？ | **いいえ** — 5803, 5016 等でも損失。9984 は量・損失とも最大 |
| EXIT は共通？ | **はい** — BF ~76%、hard_stop 16 件 |
| 寄り直後だけ？ | **傾向は共通** — opening 4/4 銘柄がマイナス、全体でも最悪バンド |
| tier で救える？ | **現データでは未確認** — A/B proxy とも全負け |
| 個別パッチ | **禁止** — 上記はすべて共通パラメータ・ゲート候補 |

---

## 次に触るべきロジック（優先順）

1. **kabu_exit_v1 — `breakout_failure`**（`fail_buffer_pct`, `fail_window_sec`）— 全銘柄・全 tier 共通スイープ  
2. **ENTRY 共通ゲート** — 寄り後 N 分（時間帯専用モジュールは作らず、既存シグナルに `session_minutes` 条件を1つ追加する程度）  
3. **hard_stop** — 16 件・最大損 -4.94% のテールを共通閾値で確認  
4. **trades 発火率** — 17/27 銘柄ゼロは分析サンプル不足。合成 PUSH 密度 or `entry_score_min` の **共通**緩和をリプレイで AB  
5. ~~9984 専用閾値~~ — **しない**

---

## 関連

- [replay.md](replay.md)
- [data_inventory.md](data_inventory.md)
- [universe.md](universe.md)
