# Phase 8: 全銘柄共通パラメータスイープ

## 目的

個別銘柄最適化ではなく、`universe_intraday_full.csv`（27銘柄）全体に効く **共通ルール** を OFAT（一次要因ずつ）で探索する。

## 実行条件

| 項目 | 値 |
|------|-----|
| ユニバース | `kabu_native/data/universe/universe_intraday_full.csv`（27銘柄・全行 passed） |
| 期間 | 2026-04-10 〜 2026-05-15 |
| キャッシュ | 540 symbol-days（intraday 1m → synthetic PUSH） |
| リプレイ設定 | `kabu_native/configs/replay.yaml`（tier B, entry_score_min 60, relaxed_signal false） |
| ベースライン EXIT | fail_window 2分, fail_buffer 0.10, bf_confirm 1, no_entry_until 09:00, hard_stop 1.20% |

再実行:

```bash
python kabu_native/scripts/run_phase8_sweep.py \
  --start-date 2026-04-10 --end-date 2026-05-15 \
  --report-date 20260516 --workers 4
```

## 出力

- [phase8_sweep_20260516.csv](../results/reports/phase8_sweep_20260516.csv)
- [phase8_sweep_20260516.json](../results/reports/phase8_sweep_20260516.json)

除外ルール: `trades < max(45, baseline_trades × 0.55)` → `excluded_low_trades=true`（今回の28設定はすべて通過）

---

## ベースライン

| 指標 | 値 |
|------|-----|
| trades | 83 |
| symbols_with_trades | 10 / 27 |
| total_pnl_pct | **-70.34** |
| profit_factor | 0.0 |
| max_loss_pct | -4.94 |
| breakout_failure_exit_count | **63** (76%) |
| hard_stop_count | 16 |
| opening_trade_count (09:00-09:30 エントリー) | **27** |
| pnl_concentration_top_symbol | 9984.T (48%) |

---

## 1. breakout_failure — 過剰早逃げか？

### 結論: **はい（confirm=1 では BF が支配的）**

- ベースラインの **76%** が `breakout_failure` 決済。
- `fail_buffer_pct` を **0.05** に狭めると PnL は **悪化**（-70.89、BF 65件）。早逃げが増える方向。
- `fail_window` を 1→3 分に延ばしても **confirm=1** では PnL はほぼ不変（±0.5% 以内）。ウィンドウ単独では効かない。
- `fail_buffer_pct=0.20` では BF が **43〜45件** に減り total_pnl **-64.25** まで改善するが、まだ損失は大きい。

### confirm_count=2 の効果（採用候補の核）

| 代表設定 | trades | total_pnl | BF件数 | hard_stop | 備考 |
|----------|--------|-----------|--------|-----------|------|
| baseline | 83 | -70.34 | 63 | 16 | — |
| fw2 buf0.12 **cc2** | 67 | **-48.58** | **16** | 17 | BF急減、他EXITへシフト |
| fw3 buf0.05 **cc2** | 67 | **-47.35** | **19** | 17 | 同上・わずかに最良 |
| fw1 buf0.05 cc1 | 83 | -70.89 | 65 | 16 | 早逃げ増 |

- **confirm_count=2** は BF シグナルを2ティック連続で要求する実装（`sweep_runner.replay_cached`）。
- trades は 83→**67**（-19%）だがフロア45を上回る。win_rate が **0→13%**、PF が **0→0.05** 程度に改善。
- max_loss は全設定で **同一 (-4.94%)**。BF調整は「最悪1トレード」ではなく **損失の積み上げ** を変えている。

**判断**: 現行 BF（buffer 0.10, confirm 1）は **寄り付き直後のノイズでも BF になりやすい**。共通ルールとしては **confirm_count=2** または **buffer 0.20** のどちらかが必要。confirm=2 の方が PnL 改善幅が大きい。

---

## 2. 寄り後ゲート（no_entry_until）

### 結論: **効果あり。09:30 ゲートが最も定量化しやすい**

| no_entry_until | trades | total_pnl | opening_trade_count | Δpnl vs baseline |
|----------------|--------|-----------|---------------------|------------------|
| 09:00 / 09:05 | 83 | -70.34 | 27 | 0 |
| 09:10 | 68 | -54.27 | 12 | **+16.1** |
| 09:15 | 62 | -48.35 | 6 | **+22.0** |
| **09:30** | **56** | **-42.39** | **0** | **+27.9** |

- 09:00 と 09:05 は **同一**（エントリーが実質 09:05 以降に集中しているため）。
- 09:30 までエントリー禁止で **opening 損失帯をゼロ化**し、total_pnl が **約40%改善**（まだマイナス）。
- trades 減（83→56）はあるが、除外閾値（45）内。

**判断**: 寄り後 30 分の共通エントリーゲートは、Phase 7 の「opening 帯が悪い」仮説を **数値で支持**する。

---

## 3. hard_stop（-0.8% 〜 -1.35%）

### 結論: **浅くしても PnL / PF は改善しない（今回の期間）**

| hard_stop_pct | trades | total_pnl | BF | hard_stop |
|---------------|--------|-----------|-----|-----------|
| 0.80 | 83 | -70.34 | 53 | **26** |
| 1.00 | 83 | -70.34 | 58 | 21 |
| 1.20 (baseline) | 83 | -70.34 | 63 | 16 |
| 1.35 | 83 | -70.34 | 68 | 11 |

- **total_pnl・max_loss・PF は全4水準で完全一致**。
- 浅い hard_stop は BF と **ラベル交換**するだけ（浅い→hard_stop増、BF減）。

**判断**: 共通 hard_stop の単独調整は **採用優先度低**。BF / 寄りゲートを先に決める。

---

## 採用候補（共通パラメータ 1〜3）

いずれも **銘柄別チューニングなし**。次フェーズ（組み合わせ検証 or paper）用。

### 候補 A — 寄り後ゲート（最優先・単独効果大）

```yaml
no_entry_until: "09:30"
# 他は baseline: fail_window 2, fail_buffer 0.10, bf_confirm 1, hard_stop 1.20
```

- total_pnl: **-42.39**（baseline +27.9pt）
- opening_trade_count: **0**
- trades: 56（許容範囲）

### 候補 B — BF 二段確認（早逃げ抑制）

```yaml
fail_window_min: 2   # または 3（差は小）
fail_buffer_pct: 0.12
bf_confirm_count: 2
no_entry_until: "09:00"
hard_stop_pct: 1.20
```

- 代表: `bf_fw2_buf0.12_cc2` → total_pnl **-48.58**, trades 67, BF **16件**
- 9984 集中 **48% → 30%** 付近

### 候補 C — 中間ゲート（トレード数維持寄り）

```yaml
no_entry_until: "09:15"
```

- total_pnl: **-48.35**, trades **62**, opening **6件**
- A よりトレード多め、B より実装が単純

**非採用（今回）**: hard_stop 0.8〜1.35 の単独変更、fail_buffer 0.05、bf_confirm 1 のまま fail_window だけ変更。

---

## 次ステップ（Phase 9 想定）

1. **候補 A × B の組み合わせ**（09:30 ゲート + bf_confirm 2）を 1 本の共通設定でリプレイ。
2. paper / shadow に `no_entry_until` と `bf_confirm_count` を config 露出。
3. win_rate はまだ低いため、**エントリー側**（score / timing）は別フェーズで検証。

---

## 実装メモ

- スイープ: `kabu_native/src/replay/sweep_runner.py`
- CLI: `kabu_native/scripts/run_phase8_sweep.py`
- `bf_confirm_count`: エンジン本体未実装のため、リプレイ層で BF 連続ヒット回数をカウント
- グリッド: BF 18 + opening 5 + hard_stop 4 + baseline = **28 run**（OFAT、他軸は baseline 固定）
