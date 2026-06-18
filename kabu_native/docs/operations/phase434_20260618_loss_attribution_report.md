# Phase434 — 20260618 Loss Attribution Report

Generated: 2026-06-18  
**Verdict:** `multi_factor_failure`

調査のみ（Runtime/YAML/Entry/Exit/Order/Discord 変更なし）。

---

## Executive Summary

20260618 canonical 合計 **-233,500円 / 89 trades / stop 38件 (42.7%)** は単一要因では説明できない。

| 要因 | 寄与 | 判定 |
|------|------|------|
| **6976.T 連続ENTRY+損切り** | -88,500円 (全体の37.9%) | 主因の一つ |
| **stop後同一銘柄再ENTRY** | 29 pairs / -111,800円 | counterfactual +97,500円改善余地 |
| **Hard Stop スリッページ** | 38/38件が -1.2%超過 (平均+0.18%超過) | 実装バグより価格飛び |
| **10,000円超価格帯** | -204,500円 (全体の87.6%) | 高価格銘柄リスク支配 |
| **capital sim vs canonical** | -98,200 vs -233,500 | **仕様差**（CAP5受理subset） |

---

## P0-1: 6976.T 連続ENTRY/STOP監査

### 必須回答

1. **ENTRY回数:** 7（AM 6 + PM 1）
2. **stop_hit回数:** 4（AM 4、PM 0）
3. **合計PnL:** **-88,500円(100株)**（全体 -233,500 の 37.9%）
4. **なぜ各ENTRYが通ったか:**
   - 全件 `gate_accept=True`、`entry_expectancy_score_v2≥5`（accepted event 実データ）
   - `entry_score_v2` gate threshold=3 を満たし、continuation_quality 0.32〜0.71
   - 6976 は dynamic40 universe、板厚・流動性は十分（trading_value ~1e11 band）
   - near_day_high_low / pullback_misread guard は ENTRY時点では非ブロック
5. **下落中の小反発を拾っていたか:** **Yes** — 7件中5件で直前15分リターンがマイナス（例: 09:37 ENTRY return_15m=-1.80%、day_highから-3.46%）
6. **Momentum low + Board mid 成立:** **No（0件）** — momentum_category は mid/low 混在、board は low/high が多く「low momentum + mid board」の典型パターンではない

### 6976 ENTRY時系列（canonical observer_exit）

| entry_time | entry | exit | pnl_yen | exit_reason | stop | 15m return | day_high dist |
|------------|-------|------|---------|-------------|------|------------|---------------|
| 09:20:16 | 21230 | 21330 | +10,000 | trailing_mfe | | -0.79% | -2.66% |
| 09:25:41 | 21170 | 20910 | -26,000 | stop_hit | ✓ | -1.07% | -2.93% |
| 09:37:12 | 21055 | 20695 | -36,000 | stop_hit | ✓ | -1.80% | -3.46% |
| 10:02:27 | 20890 | 20620 | -27,000 | stop_hit | ✓ | -0.71% | -4.22% |
| 10:25:02 | 20990 | 20725 | -26,500 | stop_hit | ✓ | +2.44% | -3.76% |
| 10:55:23 | 20715 | 20830 | +11,500 | morning_session_close | | -1.19% | -5.02% |
| 12:49:29 | 20985 | 21040 | +5,500 | trailing_mfe | | 0.00% | -0.71% |

**パターン:** 09:22の初回利確後、09:25〜10:37に **4連続損切り（-115,500円）**。いずれも当日高値圏からの押し下げ局面での再ENTRY。

Artifacts: `phase434_6976_entry_audit.csv`, `phase434_6976_price_context.csv`

---

## P0-2: 損切り後再ENTRY監査

### 必須回答

1. **stop後再ENTRY件数:** 29 pairs（当日全銘柄）
2. **stop後再ENTRY合計PnL:** **-111,800円**
3. **禁止した場合のPnL:** **-136,000円** → 実際 -233,500円より **+97,500円改善**（当日中・同一symbol・stop後再ENTRY禁止 counterfactual）
4. **Runtime反映候補か:** **Yes（研究候補）** — 6/18だけで約10万円の損失回避余地。ただし 6/17 では reentry 正寄与もあり、日次固定ルールは要バックテスト
5. **6976.T に効くか:** **Yes** — 6976 stop後reentry 6 pairs / **-98,500円**（6976損失の大部分）

### 窓別集計（抜粋）

| window | count | total_pnl | PF | worst_symbol |
|--------|-------|-----------|-----|--------------|
| 300s | 9 | -39,600 | 0.17 | 6976.T |
| 1800s | 25 | -104,300 | 0.25 | 6976.T |

Artifacts: `phase434_stop_reentry_audit.csv`, `phase434_stop_reentry_counterfactual.csv`

---

## P1-1: Hard Stop Slippage Audit

### 必須回答

1. **stop_hit件数:** 38（canonical、AM 29 + PM 9）
2. **-1.2%超過件数:** 38（100% — 全stopが閾値超過）
3. **最大超過:** **0.8651%**（worst: 3110.T 等、-2.065%）
4. **平均超過:** **0.184%**
5. **6976.T -1.7098% の原因:** entry 21055 → stop閾値 20802 → actual exit 20695。**push_gap 15s**、price_age <1s。分類 **A: PUSH間隔遅延** + 下落加速による板飛び。Hard Stopロジック未発火ではなく、**価格が1.2%を一気に貫通**
6. **stop実装バグか:** **No** — 分類 B_board_gap 52件、E_normal 28件、C 5件、A 4件。実装より **PUSH遅延・価格飛び** が主因

Artifacts: `phase434_stop_slippage_audit.csv`

---

## P1-2: 6/17 vs 6/18 Stop Rate Comparison

| day/session | trades | stops | stop_rate | PF | total_pnl |
|-------------|--------|-------|-----------|-----|-----------|
| 6/17 AM | 35 | 8 | 22.9% | 1.46 | +16,500 |
| 6/17 PM | 25 | 9 | 36.0% | 1.26 | +19,700 |
| 6/17 FULL | 60 | 17 | 28.3% | 1.33 | +36,200 |
| 6/18 AM | 70 | 29 | **41.4%** | **0.27** | **-173,000** |
| 6/18 PM | 19 | 9 | **47.4%** | **0.38** | **-60,500** |
| 6/18 FULL | 89 | 38 | **42.7%** | **0.30** | **-233,500** |

**6/18 stop率跳ねの理由（複合）:**

1. **市場局面:** 高値圏銘柄（6976, 3110 等）の日中下落トレンド。6/17は上昇継続銘柄が多く stop 22-36%
2. **6976 4連続損切り** が AM stop 29件の一部を直接押し上げ
3. **trade_count増**（60→89）で同一銘柄 churn 増加（reentry 29 pairs）
4. **高価格帯 notional 増:** 10,000円超 12 trades で -204,500円 — stop1回あたり損失額が大きい
5. reject 側は 6/18 も機能（pullback_misread 5746件 AM）だが、通過後の下落速度が速くスリッページ拡大

Artifacts: `phase434_617_vs_618_stop_comparison.csv`

---

## P2-1: 価格帯別損益

| band | trades | total_pnl | PF | stops | stop_rate |
|------|--------|-----------|-----|-------|-----------|
| <1000 | 9 | -2,400 | 0.40 | 4 | 44% |
| 1000-3000 | 55 | +4,500 | 1.08 | 20 | 36% |
| 3000-10000 | 13 | -31,100 | 0.17 | 6 | 46% |
| **10000+** | **12** | **-204,500** | **0.14** | **8** | **67%** |

**必須回答:** **Yes — 高価格銘柄（特に10,000円超）が損失を支配**（-204,500 / -233,500 = 87.6%）。6976.T は単体で -88,500円。

Artifacts: `phase434_price_band_pnl.csv`

---

## P2-2: 1500k資産シミュ整合性

### 必須回答

1. **6/18資産シミュ日次PnL:** **-98,200円**（Phase273 1500k CAP5）
2. **Runtime canonicalとの違い:** canonical **-233,500円**（全 observer_exit 100株固定） vs capital sim **-98,200円**（受理80件、拒否9件）
3. **なぜ -233,500 と equity増加が同時に見えるか:**
   - Phase274 equity curve: **AM終了 1,521,767.98**（start 1,645,767.98 から **-124,000**）
   - **PM終了 1,547,567.98**（AM終了から **+25,800** 回復）
   - canonical PM は **-60,500** だが、capital sim は CAP5/buying-power で **約半分のトレードしか実行されない**ため PM セッションはプラス寄与
   - ユーザー観測の 1,521,767 / 1,547,567 は **Phase274 intraday equity** であり canonical 日次PnL とは別レイヤー
4. **バグか仕様か:** **仕様** — `canonical_summary` = 全paper trade損益、`phase273/274` = `load_canonical_live_config_trades` + `simulate_audited`（CAP5・信用枠）

| metric | trades | pnl |
|--------|--------|-----|
| canonical observer_exit | 89 | -233,500 |
| phase273 CAP5 1500k | 80 | -98,200 |
| phase274 AM equity delta | — | -124,000 |
| phase274 PM recovery | — | +25,800 |

Artifacts: `phase434_capital_sim_consistency.csv`

---

## 判定

**`multi_factor_failure`**

- 6976 reentry churn（`6976_reentry_failure` 要素）
- stop slippage / 価格飛び（`stop_slippage_failure` 要素）
- 高価格帯リスク（`price_band_risk_failure` 要素）
- capital sim と canonical の乖離は仕様（`capital_sim_mismatch` ではない）

---

## Artifacts

- `results/reports/phase434_6976_entry_audit.csv`
- `results/reports/phase434_6976_price_context.csv`
- `results/reports/phase434_stop_reentry_audit.csv`
- `results/reports/phase434_stop_reentry_counterfactual.csv`
- `results/reports/phase434_stop_slippage_audit.csv`
- `results/reports/phase434_617_vs_618_stop_comparison.csv`
- `results/reports/phase434_price_band_pnl.csv`
- `results/reports/phase434_capital_sim_consistency.csv`
- `results/reports/phase434_loss_attribution_summary.json`

## Module

- `kabu_native/src/research/phase434_20260618_loss_attribution_audit.py`
- `kabu_native/scripts/run_phase434_20260618_loss_attribution_audit.py`
