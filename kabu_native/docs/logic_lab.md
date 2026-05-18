# Phase 17: Logic Lab（ロジック検証基盤）

## なぜ paper_trade の前に必要か

現状の replay では **銘柄数に対してトレードが極端に少ない**、または **損益がマイナス** のまま進むケースがある。  
この状態で **paper_trade** や **Discord 実運用通知** に進むと、問題が「運用」ではなく **ENTRY/EXIT ロジック** にあるか切り分けできない。

Logic Lab は次を目的とする。

| 目的 | 内容 |
|------|------|
| 診断 | 各銘柄・各時点で **何の条件で落ちたか**（`reject_reasons`） |
| 分離 | **candidate**（通知候補） / **entry**（仮想建玉） / **exit**（仮想決済）を別集計 |
| 横比較 | 固定ルールの **ロジックプロファイル** を複数日・全銘柄で比較 |
| 採用判断 | paper_trade 再開の可否を **過学習なし** の基準で検討 |

**paper_trade は当面停止扱い。** Discord は `test_discord_notify` / replay 検証用のみ。

## 過学習禁止ルール

以下は **禁止**（プロファイル追加・パラメータ調整のガイドライン）。

| 禁止 | 理由 |
|------|------|
| 特定銘柄だけ閾値を変える | 銘柄依存の過学習 |
| 特定日だけルールを変える | 日付依存の過学習 |
| 特定時刻だけ ENTRY を許可する | 時刻最適化の過学習 |
| **trade 数だけ減らして PF を見せる** | サンプル不足で見かけの改善 |

許可されるのは **全銘柄・全対象日に同一適用** の構造ルール（例: 市場セッション 09:05–14:50、BF 確認 2 本）のみ。

## ロジックプロファイル（固定）

| プロファイル | 概要 |
|--------------|------|
| `baseline` | 既定 `kabu_signal_v1` + ENTRY score≥60 + timing_ok |
| `relaxed_entry` | 合成 PUSH 向け緩和シグナル + やや低い ENTRY 閾値 |
| `continuation_v1` | baseline + **EXIT breakout_failure を 2 本確認**（shadow 系） |
| `breakout_v1` | breakout 重視・ENTRY 閾値やや緩和 |
| `vwap_trend_v1` | VWAP 乖離を厳しめ（構造フィルタ） |
| `volume_confirm_v1` | G6/G7（出来高系）reject が無い場合のみ ENTRY 寄り |

プロファイルは **コード内 `build_profiles()` で定義**（日付・銘柄ごとのチューニングはしない）。

## candidate / entry / exit の定義

| 段階 | 意味 | 集計 |
|------|------|------|
| **candidate** | 通知・品質確認の候補（プロファイルごとの `is_candidate`） | `candidate_count`, `candidates_by_profile.csv` |
| **entry** | 仮想ポジションを建てた回数 | `entry_signal_count`, `entry_count`（約定トレード数） |
| **exit** | `kabu_exit_v1` により決済したトレード | `trades`, `exit_reason` 分布 |

## 使い方

### 前提

- 1分足: `kabu_native/data/intraday_1m/` または `data/intraday_1m/`
- 銘柄: universe CSV（**passed=true 推奨・全行評価**）

### 実行

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv
```

一部銘柄のみ:

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-12 \
  --end-date 2026-05-15 \
  --symbols 9984.T,8306.T \
  --profiles baseline,relaxed_entry,continuation_v1
```

## 出力

`kabu_native/results/research/logic_lab/YYYYMMDD/run_HHMMSS/`

| ファイル | 内容 |
|----------|------|
| `profile_summary.csv` / `.json` | プロファイル別 KPI + baseline 比較フラグ |
| `trades_by_profile.csv` | 全仮想トレード |
| `candidates_by_profile.csv` | 候補イベント一覧 |
| `rejects_by_profile.csv` | reject_reason 集計（G7 は missing/zero/below + 分位） |
| `g7_trading_value_diagnostic.json` | G7 詳細診断（old/new 分布・pass_rate） |
| `g7_definition_fix_report.json` | Phase19 定義修正レポート |
| `symbol_summary.csv` | 銘柄別（プロファイル×銘柄） |
| `day_summary.csv` | 日別（プロファイル×日） |
| `entry_v2_comparison.csv` / `.json` | Phase23: baseline 比較 / Phase24: v1 基準比較 |
| `entry_v2_deep_dive.csv` / `.json` | Phase24: pullback/momentum 深掘り（`--entry-v2-phase24`） |
| `entry_v2_candidate_trades.csv` | Phase24: トレード別 entry 特徴 + post-entry 1/3/5 分 |
| `entry_v2_daily_summary.csv` / `entry_v2_symbol_summary.csv` | Phase24: 日別・銘柄別 |
| `momentum_v4_early_move_analysis.json` | Phase26: 初動逆行分析（`--momentum-v4-phase26`） |
| `momentum_v4_comparison.csv` / `.json` | Phase26: v4 vs v2 |
| `momentum_v5_recovery_analysis.json` | Phase27: 回復仮説・winner/loser 60秒比較 |
| `momentum_v5_comparison.csv` / `.json` | Phase27: v5 vs v2（`--momentum-v5-phase27`） |
| `microstructure_analysis.json` | Phase28: spread/imbalance/VWAP 初動比較（v2 enriched 参照） |
| `momentum_v6_comparison.csv` / `.json` | Phase28: v6 vs v2（`--momentum-v6-phase28`） |
| `momentum_v7_comparison.csv` / `.json` | Phase29: v7 vs v2/v5/v6（`--momentum-v7-phase29`） |
| `momentum_v7_recovery_path_analysis.json` | Phase29: recovery_hold / adverse_cut / delayed_imb 効果 |
| `momentum_v8_comparison.csv` / `.json` | Phase30: v8 vs v2/v5/v6/v7（`--momentum-v8-phase30`） |
| `recovery_persistence_analysis.json` | Phase30: reclaim/favorable persistence、一時回復 vs 継続回復 |
| `momentum_v9_comparison.csv` / `.json` | Phase31: v9 state persistence vs v8（`--momentum-v9-phase31`） |
| `state_persistence_analysis.json` | Phase31: bullish/bearish persistence、state transition |
| `momentum_v10_comparison.csv` / `.json` | Phase32: v10 transition vs v9（`--momentum-v10-phase32`） |
| `state_transition_analysis.json` | Phase32: transition path、recovery/collapse duration |
| `momentum_v11_comparison.csv` / `.json` | Phase33: v11 duration weighted vs v10（`--momentum-v11-phase33`） |
| `duration_weight_analysis.json` | Phase33: bullish/bearish weighted score、decay、hold success |
| `momentum_v12_comparison.csv` / `.json` | Phase34: v12 bullish continuation vs v11（`--momentum-v12-phase34`） |
| `bullish_continuation_analysis.json` | Phase34: continuation duration、decay/recovery、hold success |
| `momentum_v13_comparison.csv` / `.json` | Phase35: v13 momentum continuation vs v12（`--momentum-v13-phase35`） |
| `continuation_momentum_analysis.json` | Phase35: momentum continuation duration、decay/weakness、hold success |
| `research_exit_report.json` / `.csv` | Phase36: 研究終了判定・OOS 準備度・過学習リスク |
| `phase_progression_analysis.json` | Phase36: Phase25–35 の PF/複雑度/improvement decay |
| `validation_freeze_report.json` | Phase37: Complexity Freeze・研究判定（4択） |
| `oos_validation_report.json` | Phase37: IS vs OOS（4月・5/16以降）プロファイル横断 |
| `regime_validation.json` | Phase37: レジーム別 continuation / PF 安定性 |
| `paper_trade_readiness.json` | Phase37: paper trade ゲート合格状況 |
| `extended_oos_validation.json` | Phase38: 拡張 OOS・drift（PF/continuation/false hold 等） |
| `expanded_regime_validation.json` | Phase38: crash/gap/liquidity レジーム durability |
| `continuation_quality_distribution.json` | Phase38: continuation quality ranking |
| `small_scale_paper_report.json` | Phase38: 小規模 paper 検証（品質フィルタ + 同時3） |
| `small_paper_top_quartile_report.json` | Phase39: top quartile exposure gate 検証 |
| `small_paper_top_quartile_trades.csv` | Phase39: gate 通過トレード |
| `small_paper_top_quartile_rejects.csv` | Phase39: gate 拒否（理由付き） |
| `top_quartile_oos_validation.json` | Phase40: IS+OOS gate 検証・combined 判定 |
| `top_quartile_oos_summary.csv` | Phase40: ウィンドウ別比較表 |
| `top_quartile_oos_trades.csv` / `top_quartile_oos_rejects.csv` | Phase40: 全ウィンドウ通過/拒否 |
| `small_paper_gate_diagnosis_*.json` / `*.csv` | Phase43: pilot ゲート未達診断 |
| `data_availability_for_oos.json` | Phase41: intraday_1m / push_jsonl 棚卸し |
| `latest_oos_window.json` | Phase41: OOS ウィンドウ status + replay path |
| `small_paper_gate_diagnosis_*.json` | Phase43: pilot ゲート未達の診断 |
| `risk_layer_report.json` | Phase38: 損失クラスタ・レジーム DD |
| `paper_trade_readiness_v2.json` | Phase38: readiness v2 + 3択判定 |

### 主要指標

- `candidate_count`, `candidates_per_day`
- `entry_signal_count`, `entries_per_day`, `trades`
- `win_rate`, `total_pnl_pct`, `avg_pnl_pct`, `median_pnl_pct`
- `max_loss_pct`, `profit_factor`
- `mfe_ge_0_3_pct_rate`, `mfe_ge_0_5_pct_rate`, `avg_mfe_pct`, `avg_mae_pct`
- `exit_reason_counts`, `symbols_with_trades`, `trades_per_day`
- `eval_count`, `breakout_count`, `reject_top`
- `top_reject_reason`, `top_reject_is_data_quality_issue`, `possible_threshold_too_strict`
- `g7_source`, `g7_threshold`, `g7_pass_rate`
- `session_cumulative_trading_value_p90`, `minute_trading_value_p90`
- `g7_board_trading_value_p90`, `g7_below_threshold_count`, `g7_diagnosis_notes`

| 追加成果物 | 内容 |
|------------|------|
| `g7_definition_fix_report.json` | Phase18→19 の G7 定義修正前後（pass_rate・ENTRY 数） |
| `g5_diagnostic_report.json` | Phase20 G5 有効性診断（pass/reject 比較） |
| `g5_pass_vs_reject.csv` | G5 pass/reject 別の forward・trade 品質 |
| `g5_rejected_but_extended.csv` | G5 reject 後に伸びたイベント一覧 |
| `g5_symbol_summary.csv` | 銘柄別 G5 pass/reject 件数 |

`profile_summary` 追加列: `g5_pass_count`, `g5_reject_count`, `g5_pass_rate`, `trades_after_g5`, `candidates_after_g5`, `g5_is_alpha_positive`, `g5_possible_overfilter`, `g5_rejected_mfe_rate`, `g5_pass_pf`

| 追加成果物 (Phase 21) | 内容 |
|----------------------|------|
| `g6_diagnostic_report.json` | G6 定義・pass/reject・G5交差 |
| `g6_pass_vs_reject.csv` | G6 品質比較 |
| `g6_rejected_but_extended.csv` | G6 reject 後の forward 伸び |
| `g6_symbol_summary.csv` | 銘柄別 G6 |
| `g5_g6_intersection.csv` | G5×G6 4象限件数 |

`profile_summary` 追加列 (Phase 21): `g6_pass_count`, `g6_reject_count`, `g6_pass_rate`, `trades_after_g6`, `candidates_after_g6`, `g6_is_alpha_positive`, `g6_possible_overfilter`, `g6_rejected_mfe_rate`, `g6_pass_pf`, `g5_g6_both_pass_count`

| 追加成果物 (Phase 22) | 内容 |
|----------------------|------|
| `g3_diagnostic_report.json` | G3 定義・pass/reject・G5/G6 交差 |
| `g3_pass_vs_reject.csv` | G3 品質比較 |
| `g3_rejected_but_extended.csv` | G3 reject 後の forward 伸び |
| `g3_symbol_summary.csv` | 銘柄別 G3 |
| `g3_g5_g6_intersection.csv` | G3×G5×G6 象限・3ゲート通過 PF |

`profile_summary` 追加列 (Phase 22): `g3_pass_count`, `g3_reject_count`, `g3_pass_rate`, `trades_after_g3`, `candidates_after_g3`, `g3_is_alpha_positive`, `g3_possible_overfilter`, `g3_rejected_mfe_rate`, `g3_pass_pf`, `g3_g5_g6_all_pass_count`, `three_gate_profit_factor`

### Phase 22: `G3_VWAP_DIST` 有効性診断

**閾値変更・G3 緩和・削除はしない。** VWAP 乖離が alpha か ENTRY 阻害・高値掴みかを統計で判断。

**G3 が見ているもの（`g3_definition`）**

| 項目 | 意味 |
|------|------|
| `vwap_distance_pct` | `(price - vwap) / vwap × 100` |
| `threshold` | 既定 **0.35%**（`vwap_distance_pct_min`）未満で `G3_VWAP_DIST` |
| reject 内訳 | `missing` / `below_threshold` |
| `above_risk_band` | 診断のみ: pass かつ乖離 ≥ **1.5%**（高値掴みリスク帯、ゲートは変更しない） |

**G3×G5×G6（`g3_g5_g6_intersection.csv`）**

| キー | 意味 |
|------|------|
| `g3_g5_g6_all_pass` | 3 ゲートすべて通過 |
| `g3_pass_g5_reject` | VWAP OK・高値ブレイク NG |
| `g3_pass_g6_reject` | VWAP OK・出来高 NG |
| `g5_g6_pass_g3_reject` | G5/G6 OK・VWAP NG（G3 が追加壁） |
| `three_gate_profit_factor` | 3 ゲート通過 ENTRY の PF |

**判断基準**

| G3 が有効 (alpha) | G3 が過剰 |
|-------------------|-----------|
| `g3_pass_pf` または `three_gate_profit_factor` > 1 | `g3_rejected_mfe_rate` ≥ 25% |
| pass 側 MFE↑・BF↓ | `g3_pass_rate` が極端に低い |
| MAE 改善 | PF 改善なく reject 後に伸び多い |

**G5/G6 との関係:** reject 件数だけでは G5≈G6 > G3。ENTRY 母集団は **`g3_g5_g6_all_pass`**（Phase21 時点で both_pass 1,737 付近）を ENTRY v2 の設計母集団とする。

### Phase 23: ENTRY v2 プロトタイプ比較

Phase 20–22 で G3/G5/G6 はいずれも **単体では過剰フィルタ疑い** がある一方、通過トレードの PF は低い。**閾値緩和・ゲート削除だけでは改善しない** ため、ENTRY 設計そのものを複数プロファイルで横比較する。

**思想**

| 原則 | 内容 |
|------|------|
| candidate / entry 分離 | 通知候補と建玉は別閾値・別集計 |
| G3/G5/G6 は削除しない | `gate_component_scores()` で部分点化（reject 時 0.35 ペナルティ） |
| 流動性のみハードブロック | G1/G2/G7 は ENTRY 前に必須 |
| Logic Lab のみ | paper_trade / shadow には未接続 |
| 固定閾値 | 銘柄・日・時刻ごとの最適化禁止 |

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --entry-v2-comparison
```

実装: `kabu_native/src/research/entry_v2.py` + `logic_lab.replay_profile_symbol_day()` の v2 分岐。

| プロファイル | 狙い | 主な条件イメージ |
|-------------|------|------------------|
| `continuation_score_v2` | ブレイク単発ではなく **継続性スコア** で ENTRY | VWAP 上・高値接近・分足 TV・高値更新連続・G3/G5/G6 合成 score≥閾値 |
| `pullback_vwap_v1` | 高値ブレイク追随ではなく **VWAP 押し目後の再上昇** | price>VWAP、直近高値から一度押すが VWAP 割れず再上昇、流動性 OK |
| `breakout_confirm_v2` | **1〜2 本確認** して飛びつき抑制 | trigger 突破→次評価で trigger 上維持、分足 TV、BF 即死低減 |
| `momentum_volume_v1` | G6 を単体ゲートにせず **価格＋出来高の複合** | 価格モメンタム正・分足 TV 増・VWAP 上・高値接近 |
| `candidate_only_v1` | **すぐ ENTRY せず候補レイヤ** の乖離観測 | candidate 閾値 < entry 閾値（候補数 vs ENTRY 数） |

**成果物:** `results/research/logic_lab/YYYYMMDD/run_*/entry_v2_comparison.csv` / `.json`

主要指標: `eval_count`, `candidate_count`, `entry_count`, `trades_per_day`, `symbols_with_trades`, `win_rate`, `total_pnl_pct`, `profit_factor`, `max_loss_pct`, `mfe_ge_0_3_pct_rate`, `mfe_ge_0_5_pct_rate`, `avg_mae_pct`, `breakout_failure_rate`, `median_hold_min`, `top_reject_reason`, `concentration_top_symbol` など。JSON の `vs_baseline.recommended` はヒューリスティック参考値。

**採用基準（vs baseline、すべて満たす候補を paper_trade 前検討）**

- `candidate_count` または `entry_count` が baseline 以上
- `profit_factor` 改善（または同等で MFE 改善が明確）
- `mfe_ge_0_3_pct_rate` 改善
- `breakout_failure_rate` 低下
- `max_loss_pct` が baseline より悪化しない
- `concentration_top_symbol_pct` < 50%（特定銘柄依存が強くない）

**不採用基準**

- trade 数だけ増えて PF 悪化（`trade_count_up_pf_down`）
- G3/G5/G6 を単純削除・無効化しただけの見かけ改善
- 特定銘柄・特定日・特定時刻への閾値チューニング
- paper_trade 接続前に Logic Lab で再現できない改善

**Phase 23 結論（ENTRY v2 横比較）**

| 系統 | 結果 |
|------|------|
| `baseline` / `breakout_confirm_v2` | 相対的に弱い。breakout 飛びつき型の延命は不採用 |
| `continuation_score_v2` / `candidate_only_v1` | trade 数は増えやすいが PF 改善に乏しい |
| `pullback_vwap_v1` / `momentum_volume_v1` | **相対的に有望**。今後の改善母集団 |

**breakout 型を主役から外す理由:** BF 即死・通過後 PF 低迷が Phase20–22 と整合。単純な trigger 確認延長では MFE/BF が改善しにくかった。

**pullback / momentum へ移行する理由:** VWAP 上の構造（押し目再上昇）と価格＋出来高の複合は、ゲート削除なしで ENTRY 設計を変えられる。

### Phase 24: ENTRY v2 深掘り（pullback / momentum 系）

Phase23 有望候補 `pullback_vwap_v1` / `momentum_volume_v1` の詳細診断と v2 / hybrid 改善案の比較。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --entry-v2-phase24
```

| プロファイル | 内容 |
|-------------|------|
| `pullback_vwap_v1` / `momentum_volume_v1` | 深掘り対象（日別・銘柄別・分布・entry 時特徴量） |
| `pullback_vwap_v2` | v1 改善: VWAP 割れ禁止・押し幅 0.12–0.45・spread 悪化除外・深押し除外 |
| `momentum_volume_v2` | v1 改善: mom AND 出来高増・VWAP 上・即反落除外・spread 悪化除外 |
| `hybrid_vwap_momentum_v1` | 両系統の共通スコア（breakout_event 非必須） |

**成果物**

| ファイル | 内容 |
|----------|------|
| `entry_v2_deep_dive.csv` / `.json` | v1 深掘り + 全 Phase24 プロファイル要約 |
| `entry_v2_candidate_trades.csv` | エントリ時特徴量 + 1/3/5 分 post-entry 最大上下 |
| `entry_v2_daily_summary.csv` | 日別成績 |
| `entry_v2_symbol_summary.csv` | 銘柄別成績 |
| `entry_v2_comparison.csv` / `.json` | **v1 を基準** とした v2 / hybrid 採用判定 |

**Phase 24 採用基準（baseline ではなく v1 比較）**

- `pullback_vwap_v2` → 基準 `pullback_vwap_v1`
- `momentum_volume_v2` → 基準 `momentum_volume_v1`
- `hybrid_vwap_momentum_v1` → 基準 `pullback_vwap_v1`（JSON に `vs_momentum_volume_v1` も付与）

満たすこと: PF 改善、avg_pnl 改善、max_loss 悪化なし、MFE +0.3% 到達率改善、BF 率悪化なし、`symbols_with_trades` が極端に減らない、銘柄集中 < 50%、trade 数だけ増えて PF 低下しない。

### Phase 25: momentum_volume_v2 損失分析と v3 改善

Phase24 で `momentum_volume_v2` が最有力候補になったが PF はまだ実運用不可。損失構造を分析し、市場構造ベースの v3 ガードを検証する。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v3-phase25
```

**v2 を軸にする理由:** pullback 系より PF・汎化のバランスが良く、出来高＋モメンタムの複合が Phase20–22 の知見（G6 単体ゲート化しない）と整合する。

**過学習禁止（Phase25 再掲）**

| 禁止 | 例 |
|------|-----|
| 銘柄専用 | 9984 だけ閾値変更 |
| 日付専用 | 2026-05-08 だけルール変更 |
| 時刻専用 | 09:xx だけ ENTRY 許可 |
| trade 数だけ削減 | ENTRY を厳しくして PF だけ見せる |

**v3 プロファイル**

| プロファイル | 内容 |
|-------------|------|
| `momentum_volume_v3_entry_guard` | spread/imbalance/VWAP乖離/過熱mom/TV-only 等の ENTRY 共通フィルタ |
| `momentum_volume_v3_exit_guard` | imbalance EXIT を連続5本以上（MFE 時は7本）に緩和 |
| `momentum_volume_v3_take_guard` | MFE≥0.3% 後の peak から 0.18% 戻りで structural take |
| `momentum_volume_v3_combined` | entry + exit + take |

**成果物**

| ファイル | 内容 |
|----------|------|
| `momentum_v2_loss_analysis.json` / `.csv` | v2 の exit_reason・負け/勝ち・汎化チェック |
| `momentum_v3_comparison.json` / `.csv` | v2 基準の v3 採用判定 |

**v3 採用基準（vs momentum_volume_v2）**

- PF・avg_pnl 改善、max_loss 悪化なし
- `board_imbalance_exit_rate` 改善、MFE +0.3% 到達率が大きく悪化しない
- `symbols_with_trades` が極端に減らない
- `trade_count_only_reduction_improvement` フラグ付きは不採用

**paper_trade 再開条件（参考）**

- Logic Lab で v3 `recommended=true` が複数日・複数銘柄で再現
- 銘柄集中 < 50%、losing_day が支配的でない
- shadow 接続前に exit_guard / take の live 整合を別 Phase で確認

### Phase 26: Early Adverse Move（初動逆行）と protection v4

**仮説:** Phase25 で imbalance EXIT 過敏が主因と分かった一方、**losers は ENTRY 後 1〜3 分で逆行しやすい**。ENTRY 時点では winner/loser の判別は難しいが、**ENTRY 直後の価格・板の挙動には差**がある。ENTRY 条件を厳しくするのではなく **ENTRY 後監視** で保護できるかを検証する。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v4-phase26
```

**過学習禁止（再掲）:** 銘柄・日付・時刻・9984 等の個別条件禁止。trade 数削減だけの改善禁止。market structure のみ。

| プロファイル | 内容 |
|-------------|------|
| `momentum_volume_v4_early_guard` | 60秒以降: 逆行+板悪化+VWAP reclaim 失敗の複合で `early_adverse_guard` |
| `momentum_volume_v4_recovery_guard` | 初動逆行後の回復時は hard_stop / imbalance EXIT を抑制 |
| `momentum_volume_v4_imbalance_confirm` | imbalance EXIT は連続悪化+逆行同時のみ |
| `momentum_volume_v4_combined` | 上記すべて |

**成果物**

| ファイル | 内容 |
|----------|------|
| `momentum_v4_early_move_analysis.json` | v2 トレードの 15/30/60/90/180秒 比較・winner/loser・adverse first 率 |
| `momentum_v4_comparison.csv` / `.json` | v2 基準の v4 採用判定 |

**v4 採用基準（vs momentum_volume_v2）**

- PF・avg_pnl 改善、max_loss 悪化なし
- `hard_stop_rate` 減少、`board_imbalance_exit_rate` 減少
- MFE +0.3% 到達率が大きく悪化しない
- `symbols_with_trades` 維持、集中 < 50%
- `trade_count_only_reduction_improvement` は不採用

**ENTRY 品質 vs EXIT 過敏の切り分け:** `diagnosis_notes` の `imbalance_exit_often_exit_timing_not_entry_only` と `60s_adverse_separates_losers_from_winners` を併読する。

### Phase 27: Recovery-Based Exit v5

Phase26 で **60秒時点の early adverse** に winner/loser の差がある一方、winners は一度逆行しても回復する傾向があった。v4 の early_adverse_guard は「早く切る」方向だったため、v5 は **回復不能な逆行だけを切る** EXIT に pivot する。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v5-phase27
```

**考え方**

| 原則 | 内容 |
|------|------|
| ENTRY 不変 | `momentum_volume_v2` と同一 ENTRY |
| 60秒未満 | 単発 `board_imbalance_deterioration` では EXIT しない |
| 早期撤退 | 逆行+モメンタム負+VWAP悪化+favorable不足が同時のみ `recovery_early_cut` |
| 60秒以降 imbalance | 連続3本悪化。MFE≥0.3% 後は giveback 優先 |
| 60〜90秒 | `recovery_or_cut` で保持 vs 切断を一度判定 |

| プロファイル | ルール |
|-------------|--------|
| `momentum_volume_v5_recovery_exit` | A: recovery_exit |
| `momentum_volume_v5_delayed_imbalance_exit` | B: delayed_imbalance_exit |
| `momentum_volume_v5_recovery_or_cut` | C: recovery_or_cut |
| `momentum_volume_v5_combined` | A+B+C |

**成果物:** `momentum_v5_recovery_analysis.json`, `momentum_v5_comparison.csv` / `.json`

**v5 採用基準（vs momentum_volume_v2）**

- PF・avg_pnl 改善、`board_imbalance_exit_rate` 低下
- `hard_stop_rate`・max_loss 悪化なし、MFE +0.3% 大きく悪化しない
- `symbols_with_trades` 維持、集中 < 50%
- `trade_count_only_reduction_improvement` 不採用

**paper_trade 再開条件（参考）**

- v5 で `recommended=true` が universe 横断で再現
- 回復トレード（`recovered_after_adverse` + 正の pnl）が v2 比で維持・増加
- shadow 前に live 板データでの exit 整合確認

### Phase 28: Real-Market Microstructure Adaptation

Phase23〜27 で、Yahoo リプレイでは有効に見えた breakout 追随が、kabu 実市場では **板 imbalance / spread / 初動逆行ノイズ** で崩れるケースが多いことが確認された。Phase28 は「綺麗な breakout 追随」から **実市場マイクロ構造に耐える ENTRY/EXIT** への適応を Logic Lab 内だけで検証する（paper_trade / shadow 未接続）。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v6-phase28
```

**Yahoo replay と実市場の差**

| 観察 | 意味 |
|------|------|
| breakout 追随は BF 率が高い | トリガー直後の「飛びつき」は実板ノイズに弱い |
| ENTRY 瞬間では winner/loser 分離しにくい | 品質判定は 60 秒以降の path が重要 |
| imbalance EXIT が過敏 | 単発板ノイズで利確前に切られる |
| losers は初動逆行型が多い | ただし winners も一度逆行する |
| spread 急拡大 + imbalance 崩壊 | 「構造崩壊」のシグナルになりやすい |

**microstructure analysis（`microstructure_analysis.json`）**

ENTRY 後 15/30/60/90 秒で winner/loser を比較: spread 変化、imbalance 変化、momentum persistence、VWAP reclaim。加えて fake breakout 率、recovery 成功率、noise reversal 率を集計する。

**fake breakout 問題**

breakout 直後に VWAP 下・imbalance 急崩壊・favorable move 不足・spread 急拡大が重なると `fake_breakout_score` が上がる。v6 はスコア閾値以上で `fake_breakout_exit`（構造ルールのみ、銘柄/日/時刻最適化なし）。

**recovery bias EXIT の考え方**

- 60 秒未満: 単発 `board_imbalance_deterioration` は無視（ノイズ窓）
- 小さい逆行・VWAP reclaim・favorable persistence ありなら保持
- **spread 急拡大 + imbalance 崩壊継続 + momentum 負継続** のみ `microstructure_noise_exit`
- **VWAP reclaim 失敗継続 + imbalance 崩壊 + adverse persistence + favorable 欠如** で `structure_break_exit`

| プロファイル | ルール |
|-------------|--------|
| `momentum_volume_v6_noise_tolerant` | ノイズ許容 + 三重条件でのみ microstructure EXIT |
| `momentum_volume_v6_structure_break` | 構造崩壊スコアのみ切断 |
| `momentum_volume_v6_recovery_bias` | recovery 優先で imbalance 抑制 |
| `momentum_volume_v6_combined` | 上記統合 |

**re-entry cooldown（構造回復で解除）**

`structure_break_exit` / `fake_breakout_exit` 等の後、固定秒数ではなく **VWAP > 0.05% かつ imbalance ≥ 0.48** で再 ENTRY 許可。noise loop 抑制用（銘柄日単位）。

**過学習禁止（Phase28 も同様）**

- 特定銘柄・特定日・特定時刻への閾値チューニング禁止
- breakout 飛びつき型の延命禁止
- trade 数削減だけの「改善」は不採用（`trade_count_only_reduction_improvement` フラグ）

**v6 採用基準（vs `momentum_volume_v2`）**

- PF・avg_pnl 改善、`fake_breakout_rate` 低下、`board_imbalance_exit_rate` 低下
- `hard_stop_rate`・max_loss 悪化なし
- `symbols_with_trades` 維持、concentration 悪化なし
- `reentry_loop_risk`（再 ENTRY 暴走）なし
- trade 数だけ減らした改善は不採用

**paper_trade 候補の判断**

`momentum_v6_comparison.json` の `vs_recommended=true` と `microstructure_analysis.json` の winner/loser 分離が universe で再現すれば候補。未達なら v6 調整ではなく ENTRY 構造またはデータ品質を先に見る。

### Phase 29: v7 Noise-Tolerant EXIT（v6 絞り込み）

Phase28 で **fake breakout より実市場ノイズによる過敏 EXIT（特に imbalance_exit）** が主因と判明。winner/loser は ENTRY 後 15〜60 秒の momentum / VWAP 変化で分離する。v6 は要素が重なりすぎたため、v7 は 3 柱を分離して検証する。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v7-phase29
```

**v6 → v7 の改善理由**

| v6 の課題 | v7 の対応 |
|-----------|-----------|
| fake_breakout スコアが主因ではなかった | fake breakout 軸を廃止し EXIT 簡素化 |
| imbalance が依然多い | `delayed_imb` で 60 秒未満は imb EXIT 禁止 |
| 回復と切断が混在 | `recovery_check` を 60 秒一点で判定 |
| 構造崩壊とノイズが混同 | `structure_break_only` で多条件同時のみ切断 |

**1. delayed_imbalance**

- ENTRY 後 60 秒未満: 単発 `board_imbalance_deterioration` では EXIT しない
- 60 秒以降: 連続悪化（streak ≥ 4）のみ
- `hard_stop` / `vwap_collapse_exit` は別扱い

**2. recovery_check（15〜60 秒、60 秒時点で判定）**

保持: `momentum_change ≥ 0` OR `vwap_distance_change ≥ 0` OR `favorable_move ≥ +0.05%`

切断: 上記を満たさず、`momentum < 0` AND `vwap_change < 0` AND `favorable < +0.03%` AND `adverse ≤ -0.12%` → `v7_adverse_cut`

**3. structure_break_only**

VWAP reclaim 失敗継続 + momentum 負継続 + adverse persistence + imbalance collapse + favorable 不足 → `structure_break_v7`

| プロファイル | 柱 |
|-------------|-----|
| `momentum_volume_v7_delayed_imb` | 1 のみ |
| `momentum_volume_v7_recovery_check` | 2 のみ |
| `momentum_volume_v7_structure_break` | 3 のみ（imb/BF は構造以外抑制） |
| `momentum_volume_v7_combined` | 1+2+3 |

**比較セット:** `momentum_volume_v2` / `v5_combined` / `v6_combined` + v7 各種（ENTRY はすべて v2 同等）

**成果物**

- `momentum_v7_comparison.csv` / `.json` — v2/v6 採用フラグ付き
- `momentum_v7_recovery_path_analysis.json` — judgment 別 winner/loser 15/30/60s、recovery_hold / adverse_cut / delayed_imb 効果

**v7 採用基準**

- vs v2: PF 改善、imbalance_exit_rate 低下、hard_stop / max_loss / MFE 悪化なし
- vs v6: avg_pnl 改善
- symbols_with_trades 維持、concentration 悪化なし
- trade 数だけの改善・`reentry_loop_rate` 悪化は不採用

**paper_trade 再開条件（参考）**

- `momentum_v7_combined` で `vs_v2_recommended=true` かつ `v6_vs_v6_avg_pnl_improved=true`
- `momentum_v7_recovery_path_analysis.json` で recovery_hold が正の avg_pnl、adverse_cut が tail 抑制、delayed_imb が損失先送りのみでないこと

### Phase 30: v8 Recovery Persistence EXIT

Phase29 で **一時回復はあるが継続回復かは別** と判明。v8 は「60 秒時点のスナップショット」ではなく **reclaim / favorable / imbalance の persistence** で保持・切断する。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v8-phase30
```

**一時回復 vs 継続回復**

| 一時回復（切る） | 継続回復（保持） |
|----------------|----------------|
| reclaim 後すぐ再悪化 (`reclaim_failure_exit`) | reclaim 維持 (`reclaim_persistent`) |
| favorable スパイク後失速 (`favorable_fade_exit`) | favorable persistence 継続 |
| recovery_then_fail | recovery_then_trend |
| imbalance 一瞬悪化のみ | imbalance + adverse 継続 |

**4 柱 + combined**

| プロファイル | 内容 |
|-------------|------|
| `momentum_volume_v8_reclaim_persistence` | VWAP reclaim 維持 vs 再悪化 |
| `momentum_volume_v8_favorable_persistence` | favorable 継続 vs fade |
| `momentum_volume_v8_delayed_imb_refined` | 60 秒未満 imb 無視、以降は adverse 伴う継続悪化のみ |
| `momentum_volume_v8_structure_break_refined` | VWAP reclaim 失敗継続 + momentum 負 + imb collapse + favorable 消失 |
| `momentum_volume_v8_combined` | 統合 |

**比較セット:** v2 / v5_combined / v6_combined / v7_combined + v8 各種

**分析（`recovery_persistence_analysis.json`）**

- reclaim / favorable / imbalance persistence 率
- recovery_then_trend vs recovery_then_fail
- winner/loser の 15/30/60/90/180 秒 momentum・VWAP・favorable・imbalance 比較

**v8 採用基準（vs v2/v5/v6/v7）**

- PF・avg_pnl 改善、imbalance_exit_rate 低下
- hard_stop / max_loss / MFE 悪化なし
- reclaim_persistence 改善、recovery_then_trend 増、recovery_then_fail 減
- symbols_with_trades 維持、concentration / reentry_loop 悪化なし
- trade 数だけの改善は不採用

**paper_trade 再開条件（参考）**

- `momentum_volume_v8_combined` で `recommended=true`（全参照比較で flags 空）
- `recovery_persistence_analysis.json` で sustained_recovery_rate > temporary_bounce_rate
- universe 横断で v7 比 imbalance_exit 低下と avg_pnl 改善が再現

### Phase 31: v9 State-Based Persistence EXIT

Phase30 で 60 秒付近に winner/loser 差が見えても、**固定秒数ルールはレジーム変化に弱く過学習リスク**がある。v9 は **評価 tick 上の状態 persistence** で EXIT/HOLD する（`state_persistence_engine.py`）。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v9-phase31
```

**time-based vs state-based**

| time-based (v7/v8) | state-based (v9) |
|--------------------|------------------|
| 60 秒で recovery 判定 | bullish/bearish instant score の連続 tick |
| 固定 imb 遅延 | bearish persistence + adverse 継続で imb EXIT |
| レジーム非対応 | dominant_state 遷移で判定 |

**persistence engine**

| スコア | 成分 |
|--------|------|
| bullish | VWAP reclaim persistence, favorable, momentum, spread 安定, imbalance 回復 |
| bearish | adverse, VWAP failure, momentum 負, spread 悪化, imbalance collapse |

**EXIT:** `state_bearish_persistence_exit`, `state_structure_break_exit`, `state_recovery_fail_exit`  
**HOLD:** bullish/recovery persistence 継続中は imb/BF 抑制

| プロファイル | 役割 |
|-------------|------|
| `momentum_volume_v9_state_persistence` | bearish persistence EXIT |
| `momentum_volume_v9_structure_break_state` | 構造崩壊 persistence のみ |
| `momentum_volume_v9_recovery_state` | recovery 失敗遷移 |
| `momentum_volume_v9_combined` | 統合 |

**比較:** `momentum_volume_v2` / `v8_combined` + v9 各種

**分析（`state_persistence_analysis.json`）:** persistence score 分布、bullish→bearish 遷移、bearish/recovery duration、fixed_time_proxy_rate（legacy 60s 依存の代理指標）

**v9 採用基準（vs v8）**

- PF・avg_pnl 改善、imbalance_exit_rate 低下
- hard_stop / max_loss / MFE 悪化なし
- `fixed_time_proxy_rate` 悪化なし（固定時間依存の減少）
- symbols_with_trades 維持、concentration / reentry 悪化なし
- trade 数だけの改善は不採用

**paper_trade 再開条件（参考）**

- `momentum_volume_v9_combined` で `recommended=true`
- `state_persistence_analysis.json` で state_exit_rate 改善かつ fixed_time_proxy_rate ≤ v8
- recovery_then_trend 増・recovery_then_fail 減が universe で再現

### Phase 32: v10 State Transition Engine

Phase31 で **persistence 閾値より状態の流れ** が重要と判明。v10 は bullish / neutral / bearish の **遷移パス** で EXIT/HOLD する（`state_transition_engine.py`）。固定 60 秒は使わない。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v10-phase32
```

**persistence threshold → transition engine**

| v9 | v10 |
|----|-----|
| bearish_score > X で EXIT | bullish→neutral→bearish **継続** で collapse |
| 単発 bearish 無視不足 | bearish_locked（戻れない bearish） |
| recovery スコアのみ | bearish→neutral→bullish **遷移** で recovery hold |

**遷移パス**

- **Recovery:** bearish → neutral → bullish（`recovery_transition_active`）
- **Collapse:** bullish → neutral → bearish persistence（`transition_collapse_exit`）
- **EXIT:** `transition_bearish_continuation_exit`, `transition_recovery_failure_exit`
- **HOLD:** recovery transition、bullish stabilization、neutral recovery

| プロファイル | 役割 |
|-------------|------|
| `momentum_volume_v10_transition_persistence` | bearish continuation（戻れない bearish） |
| `momentum_volume_v10_recovery_transition` | recovery 遷移 / failure |
| `momentum_volume_v10_structure_transition` | collapse 遷移 |
| `momentum_volume_v10_combined` | 統合 |

**比較:** `momentum_volume_v2` / `v9_combined` + v10 各種

**分析（`state_transition_analysis.json`）:** path frequency、velocity、recovery/collapse duration、winner/loser transition 比較

**v10 採用基準（vs v9）**

- PF・avg_pnl 改善、`fixed_time_proxy_rate` 悪化なし
- collapse detection・recovery hold 改善
- hard_stop / max_loss / MFE 悪化なし
- symbols_with_trades 維持、concentration / reentry 悪化なし
- trade 数だけの改善は不採用

**paper_trade 再開条件（参考）**

- `momentum_volume_v10_combined` で `recommended=true`
- `state_transition_analysis.json` で recovery_transition_success_rate 改善かつ collapse_transition_rate 適正
- v9 比 `fixed_time_proxy_rate` 低下が universe で再現

### Phase 33: v11 Duration-Weighted Persistence EXIT

Phase32 で **状態継続時間（duration）** が winner/loser 分離に強いことが判明。v11 は「状態があるか」ではなく **duration × persistence quality** の加重スコアで EXIT/HOLD する（`duration_weighted_engine.py`）。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v11-phase33
```

**persistence duration weighting**

| 成分 | bullish weight | bearish weight |
|------|----------------|----------------|
| 継続 tick | bullish duration × quality | bearish duration（短い≤2 tickはノイズ） |
| 品質 | reclaim / favorable / momentum | adverse / collapse / VWAP failure |
| EXIT | bearish score 増加・decay・collapse weighted | |
| HOLD | bullish weighted・neutral stabilization・short bearish noise | |

| プロファイル | 役割 |
|-------------|------|
| `momentum_volume_v11_bullish_duration` | 高 bullish weighted で HOLD |
| `momentum_volume_v11_bearish_duration` | 長い bearish weighted で EXIT |
| `momentum_volume_v11_decay_detection` | bullish decay + bearish 増加 |
| `momentum_volume_v11_combined` | 統合 |

**比較:** `momentum_volume_v2` / `v10_combined` + v11 各種

**分析（`duration_weight_analysis.json`）:** weighted score 分布、decay/collapse pattern、`weighted_hold_success` vs `weighted_false_hold`

**v11 採用基準（vs v10）**

- PF・avg_pnl 改善、collapse detection 改善、`weighted_false_hold_rate` 低下
- hard_stop / max_loss / MFE 悪化なし、`fixed_time_proxy_rate` 増加禁止
- symbols_with_trades 維持、concentration / reentry 悪化なし
- trade 数だけの改善は不採用

**paper_trade 再開条件（参考）**

- `momentum_volume_v11_combined` で `recommended=true`
- winners の `bullish_weighted_score` > losers、`bearish_weighted_score` は losers 側が高いことを universe で確認

### Phase 34: v12 Bullish Continuation Prioritization EXIT

Phase33 で **bullish persistence duration / favorable / bullish weighted score** が最強特徴、collapse/structure break 単体は弱いと判明。v12 は「崩れたら切る」から **「強い限り持つ」** へシフト（`bullish_continuation_engine.py`）。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v12-phase34
```

**bullish continuation prioritization**

| 観点 | v11 中心 | v12 中心 |
|------|----------|----------|
| 判断軸 | bearish weighted / collapse | bullish continuation score × duration |
| HOLD | weighted bullish persistence | continuation が続く限り（短 bearish はノイズ） |
| EXIT | collapse / structure break 加重 | continuation 消失・decay・bearish accumulation |

| プロファイル | 役割 |
|-------------|------|
| `momentum_volume_v12_bullish_continuation` | 高 continuation で HOLD 優先 |
| `momentum_volume_v12_decay_exit` | favorable/momentum/reclaim 減衰で EXIT |
| `momentum_volume_v12_bearish_accumulation` | 長い bearish accumulation で EXIT |
| `momentum_volume_v12_combined` | 統合（continuation loss / structure deterioration 含む） |

**比較:** `momentum_volume_v2` / `v11_combined` + v12 各種

**分析（`bullish_continuation_analysis.json`）:** continuation duration 分布、decay/recovery/failure pattern、winner/loser の `bullish_continuation_score`

**v12 採用基準（vs v11）**

- PF・avg_pnl 改善、`bullish_continuation_score` 改善（winners > losers）
- `weighted_false_hold_rate` 悪化なし、hard_stop / max_loss / MFE 悪化なし
- `fixed_time_proxy_rate` 増加禁止、symbols_with_trades 維持
- concentration / reentry 悪化なし、trade 数だけの改善は不採用

**過学習禁止:** 特定銘柄・日・時刻の最適化禁止。ENTRY は `momentum_volume_v2` 維持。Logic Lab のみ（paper_trade / shadow 未接続）。

**paper_trade 再開条件（参考）**

- `momentum_volume_v12_combined` で `recommended=true`
- `bullish_continuation_analysis.json` で winners の continuation duration > losers
- v11 比 `weighted_false_hold_rate` 非悪化が universe で再現

### Phase 35: v13 Momentum Continuation Priority EXIT

Phase34 で **momentum continuation / favorable continuation / bullish continuation duration** が最強、reclaim/spread/collapse/recovery 単体は弱いと判明。v13 は **momentum continuation persistence** を最優先（`continuation_momentum_engine.py`）。

```bash
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v13-phase35
```

**momentum continuation priority**

| 観点 | v12 中心 | v13 中心 |
|------|----------|----------|
| 判断軸 | bullish continuation 全般 | momentum weighted × favorable × duration |
| HOLD | continuation score | momentum continuation が維持される限り（短 bearish / 短 fade はノイズ） |
| EXIT | continuation loss / structure | momentum 消失・decay・weakness・bearish accumulation |

| プロファイル | 役割 |
|-------------|------|
| `momentum_volume_v13_momentum_priority` | 高 momentum continuation で HOLD |
| `momentum_volume_v13_decay_exit` | momentum/favorable fade persistence で EXIT |
| `momentum_volume_v13_bearish_accumulation` | bearish accumulation persistence で EXIT |
| `momentum_volume_v13_combined` | 統合（weakness / momentum loss 含む） |

**比較:** `momentum_volume_v2` / `v12_combined` + v13 各種

**分析（`continuation_momentum_analysis.json`）:** momentum continuation duration、decay/weakness/recovery、winner/loser の `momentum_continuation_score`

**v13 採用基準（vs v12）**

- PF・avg_pnl 改善、`momentum_continuation_score` 改善（winners > losers）
- `continuation_false_hold_rate` 悪化なし、hard_stop / max_loss / MFE 悪化なし
- `fixed_time_proxy_rate` 増加禁止、symbols_with_trades 維持
- concentration / reentry 悪化なし、trade 数だけの改善は不採用

**過学習禁止:** 特定銘柄・日・時刻の最適化禁止。ENTRY は `momentum_volume_v2` 維持。Logic Lab のみ（paper_trade / shadow 未接続）。

**paper_trade 再開条件（参考）**

- `momentum_volume_v13_combined` で `recommended=true`
- `continuation_momentum_analysis.json` で winners の momentum continuation duration > losers
- v12 比 `continuation_false_hold_rate` 非悪化が universe で再現

### Phase 36: Research Exit Criteria / Validation Freeze

27銘柄 × 約15営業日では、これ以上の EXIT 複雑化は過学習リスクが高い。**新ロジック追加ではなく**、いつ研究を止めて OOS / paper trade 検証へ移るかを定量化するメタ分析（`research_exit_criteria.py`）。**paper_trade / shadow には未接続。**

```bash
# Logic Lab 実行後に単体で評価
python kabu_native/scripts/run_research_exit_criteria.py \
  --run-dir kabu_native/results/research/logic_lab/YYYYMMDD/run_HHMMSS \
  --focus-profile momentum_volume_v13_combined \
  --phase-run-root kabu_native/results/research/logic_lab

# Logic Lab 実行時に自動出力（momentum Phase25+ または明示フラグ）
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --momentum-v13-phase35 \
  --research-exit-phase36
```

#### 評価カテゴリ

| カテゴリ | 主な指標 |
|----------|----------|
| **A. Robustness** | `symbols_with_trades_ratio`, `concentration_top_symbol_pct`, `day_concentration_pct`, `regime_concentration_pct` |
| **B. Stability** | PF / avg_pnl / worst_day / max_loss の日次安定性（CV） |
| **C. Overfitting Risk** | `fixed_time_dependency_pct`, `profile_complexity_score`, phase間 PF decay, `trade_count_collapse`, `parameter_sensitivity` |
| **D. Market Structure** | momentum / bullish persistence / bearish accumulation / continuation の winner–loser 一貫性 |

#### Complexity Penalty

Phase 進行に伴う構造複雑度（銘柄・日・時刻ごとのチューニングではない）:

| 成分 | 例（Phase35） |
|------|----------------|
| state 数 | 12 |
| persistence 数 | 16 |
| weighted feature 数 | 14 |
| transition feature 数 | 5 |

`complexity_score = state×2 + persistence×1.5 + weighted×2.5 + transition×3`（閾値既定 72）

#### Diminishing Returns

`phase_progression_analysis.json` で Phase25→35 の combined プロファイルを横断比較:

- `pf_improvement_pct` … 前 Phase 比 PF 改善率
- `complexity_increase` … 複雑度増分
- `signal_noise_ratio` … PF改善 / 複雑度増

**3 Phase 連続で PF 改善 &lt; 3%** → `diminishing_returns_warning=true`（これ以上の in-sample 最適化は推奨しない）

#### Freeze Recommendation

| 判定 | 意味 |
|------|------|
| `continue_research` | まだ改善余地・OOS 未準備 |
| `freeze_and_validate` | 構造は固定し OOS / hold-out 検証へ |
| `move_to_paper_trade` | paper trade 移行条件を満たす（下表） |
| `high_overfit_risk` | 複雑度・集中・trade 崩壊・fixed-time 依存が危険域 |

#### move_to_paper_trade（初期閾値・人手確認必須）

| 条件 | 閾値 |
|------|------|
| PF | ≥ 1.10 |
| avg_pnl | &gt; 0 |
| fixed_time_dependency | &lt; 20% |
| symbols_with_trades_ratio | ≥ 0.70 |
| concentration_top_symbol | &lt; 35% |
| complexity_score | ≤ 72 |
| diminishing_returns_warning | **true**（改善が頭打ち） |
| continuation consistency | 安定（winner–loser gap 正） |

#### high_overfit_risk（例）

- `complexity_score` が閾値超
- PF improvement decay（連続低改善）
- `trade_count_collapse`（v2 比 ENTRY 急減）
- 銘柄集中悪化
- fixed-time 依存の増加

#### OOS Readiness

`research_exit_report.json` → `oos_readiness`:

- fixed-time 低依存
- 銘柄依存低
- continuation / persistence consistency 高
- false hold / hard stop 安定

6 項目中 4 以上で部分準備、`freeze_and_validate` 判断の参考。

#### 過学習停止条件（研究フェーズ）

1. **Diminishing returns** が 3 Phase 連続
2. **complexity_score** が閾値超かつ PF 改善なし
3. **trade 数だけ減って PF が上がる** パターン
4. **fixed_time_proxy_rate** が 20% 超
5. **1 銘柄集中** が 35% 超
6. 市場構造一貫性（momentum continuation）が universe で再現しない

自動で paper_trade を再開しない。`freeze_recommendation` は **警告とゲート** のみ。

### Phase 37: Validation Freeze + OOS / Regime Validation

**新 EXIT・新 persistence・新 weighting・新 transition は禁止。** v10–v13 combined と `momentum_volume_v2` / `baseline` のみ検証対象（`phase37_validation.py`）。paper_trade / shadow 未接続。

```bash
# IS 実行済み run に対し OOS リプレイ + レポート
python kabu_native/scripts/run_phase37_validation.py \
  --is-run-dir kabu_native/results/research/logic_lab/YYYYMMDD/run_HHMMSS \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --run-oos

# Logic Lab から一括（IS + OOS 自動）
python kabu_native/scripts/run_logic_lab.py \
  --start-date 2026-05-01 --end-date 2026-05-15 \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --validation-phase37
```

#### Validation Freeze（Complexity Freeze）

| 禁止 | 許可 |
|------|------|
| 新 feature / 新 EXIT / 新 persistence・weighting・transition | v10–v13 固定プロファイルの OOS・レジーム検証 |
| 銘柄・日・時刻ごとのチューニング | 全 universe 同一ルールのリプレイ |

凍結プロファイル: `baseline`, `momentum_volume_v2`, `v10_combined` … `v13_combined`

#### OOS 期間

| ウィンドウ | 期間 |
|-----------|------|
| `oos_april` | 2026-04-01 〜 2026-04-30 |
| `oos_may_forward` | 2026-05-16 〜 データ最新日 |

IS 既定: 2026-05-01 〜 2026-05-15（`--is-run-dir` で指定）

OOS 悪化: `(IS_PF − OOS_PF) / IS_PF ≤ 15%`（プロファイル別・ウィンドウ別）

#### Regime 分類（市場プロキシ・全銘柄中央値）

| レジーム | 条件（概略） |
|----------|----------------|
| 上昇 `uptrend` | 日中リターン中央値 ≥ +0.25% |
| 下落 `downtrend` | ≤ −0.25% |
| 横ばい `sideways` | その間 |
| 高ボラ `high_vol` | 日中レンジ中央値 ≥ 1.2% |
| 低ボラ `low_vol` | レンジ ≤ 0.45% |

見るもの: continuation / momentum consistency、false hold、PF 安定、**regime_collapse**（レジーム間 PF 乖離過大）

#### Paper Trade Gate（focus 既定: `v13_combined`）

| ゲート | 閾値 |
|--------|------|
| PF | ≥ 1.05 |
| avg_pnl | &gt; 0 |
| OOS 悪化 | ≤ 15% |
| fixed-time 依存 | &lt; 20% |
| symbols coverage | ≥ 70% |
| concentration | &lt; 35% |
| false hold | 安定（≤ 45%） |
| regime collapse | なし |

#### 研究判定（`validation_freeze_report.json` → `research_decision`）

| 判定 | 意味 |
|------|------|
| `move_to_paper_trade` | 全ゲート合格（人手確認後に paper 候補） |
| `freeze_and_validate` | 凍結維持・OOS 拡張継続 |
| `continue_research` | ゲート未達・検証継続（**新ロジック追加は不可**） |
| `terminate_research` | IS/OOS とも弱くレジーム崩壊 — 戦略見直し |

### Phase 38: Extended OOS + Small-Scale Paper Validation

Phase37 で continuation / momentum / bearish accumulation の **OOS 耐久**は確認済みだが、PF&lt;1・avg_pnl&lt;0・false hold 高 — **新 EXIT 追加禁止**。汎化検証と小規模実運用検証のみ（`phase38_validation.py`）。

```bash
python kabu_native/scripts/run_phase38_validation.py \
  --reference-run-dir kabu_native/results/research/logic_lab/YYYYMMDD/run_HHMMSS \
  --universe kabu_native/data/universe/universe_intraday_full.csv \
  --run-extended-oos
```

#### Validation-only（Complexity Freeze 維持）

| 許可 | 禁止 |
|------|------|
| Extended OOS / Regime / Quality ranking / Risk / Exposure | 新 EXIT / persistence / weighting / transition |
| 小規模 paper シミュレーション（非 live） | full paper_trade 自動接続 |

#### Extended OOS 期間

| ウィンドウ | 期間 |
|-----------|------|
| `oos_march` | 2026-03-01 〜 2026-03-31 |
| `oos_april` | 2026-04-01 〜 2026-04-30 |
| `oos_may_late` | 2026-05-16 〜 最新 |
| `oos_latest` | 直近 10 営業日 |

Drift: PF / avg_pnl / continuation consistency / false hold / concentration / trade frequency（参照 IS 比）

#### Expanded Regime

`crash_like` / `gap_up` / `gap_down` / `low_liquidity` / `high_liquidity` + Phase37 基本レジーム

#### Small-Scale Paper（非本番）

| 制限 | 値 |
|------|-----|
| max concurrent | 3 |
| min continuation quality | 0.42 |
| weak momentum 除外 | &lt; 0.28 |
| bearish accumulation 除外 | &gt; 0.55 |

#### Continuation Quality Ranking

`momentum_continuation` / `bullish_duration` / `favorable` / `bearish_inverse` / `stability` — top quartile PF を tier 比較

#### Risk Layer

max loss clustering / consecutive losers / continuation collapse clustering / regime drawdown → `monetization_issue` vs `risk_exposure_issue`

#### 判定（`paper_trade_readiness_v2.json`）

| 判定 | 意味 |
|------|------|
| `move_to_small_paper` | 品質安定・drift 安定・小規模 PF/avg 改善・risk OK |
| `freeze_and_observe` | 構造は残るが収益化不明 — 観察継続 |
| `terminate_strategy` | 汎化 or 収益化不可 |

### Phase 39: Top-Quartile Small Paper Exposure Gate

Phase38 で **full book PF~0.20** だが **top_quartile（quality≥0.55）PF~1.64** と分離した。売買ロジック全体の破綻ではなく **低品質 trade の過剰 exposure** が主因 — **新 EXIT 追加禁止**。exposure / quality gate のみ（`exposure_gate.py` + `small_paper_top_quartile.yaml`）。

```bash
python kabu_native/scripts/run_phase39_top_quartile.py \
  --reference-run-dir kabu_native/results/research/logic_lab/phase38_full_20260518 \
  --config kabu_native/configs/small_paper_top_quartile.yaml \
  --universe kabu_native/data/universe/universe_intraday_full.csv
```

#### なぜ新ロジックではなく exposure か

| 観察 | 含意 |
|------|------|
| v13 EXIT は frozen・continuation OOS は安定 | EXIT 再設計より **entry 選別 / 同時保有** が効く |
| below_median PF~0.05、top quartile PF~1.64 | **品質 tier で収益が分離** — 全件売買がノイズ |
| Phase38 small paper（≥0.42）は薄い edge | **top quartile（≥0.55）** に絞って pilot 候補を判定 |

#### Exposure Gate（`exposure_gate.py`）

| 拒否理由 | 条件 |
|----------|------|
| `low_quality` | `continuation_quality` &lt; 0.55 |
| `max_concurrent` | 同時保有 ≥ 3 |
| `risk_cluster_block` | 連続損失クラスタ（設定: 5 連敗） |
| `daily_loss_guard` | 日次累計 PnL ≤ -2.5% |

ENTRY は `momentum_volume_v2` 維持。`order_enabled: false` / `discord_enabled: false` — **live・shadow 未接続**。

#### 設定（`configs/small_paper_top_quartile.yaml`）

| キー | 値 |
|------|-----|
| `profile` | `momentum_volume_v13_combined` |
| `min_continuation_quality` | 0.55 |
| `max_concurrent_positions` | 3 |
| `reject_below_quality` | true |

#### 出力

- `small_paper_top_quartile_report.json` — accepted / rejected 集計、quality tier、risk
- `small_paper_top_quartile_trades.csv` — 通過トレード
- `small_paper_top_quartile_rejects.csv` — 拒否（`gate_reject_reason`）

#### Pilot 候補ゲート（`move_to_small_paper_candidate`）

| ゲート | 閾値 |
|--------|------|
| PF | ≥ 1.20 |
| avg_pnl | &gt; 0 |
| trades | ≥ 100 |
| symbols coverage | ≥ 70% |
| concentration | &lt; 35% |
| max_concurrent | ≤ 3（観測 peak 含む） |
| risk_clustering | acceptable |

### Phase 40: Top-Quartile OOS / Extended Validation

Phase39 で IS の gate PF は改善（例: PF~2.15）だが **trades=28** でサンプル不足・coverage 未達。**新ロジック追加禁止** — Phase39 exposure gate を **OOS / Extended OOS** に適用し、結合サンプルで汎化と pilot 可否を判定（`top_quartile_oos_validation.py`）。

```bash
python kabu_native/scripts/run_phase40_top_quartile_oos.py \
  --reference-run-dir kabu_native/results/research/logic_lab/20260517/run_225513 \
  --extended-oos-json kabu_native/results/research/logic_lab/phase38_full_20260518/extended_oos_validation.json \
  --config kabu_native/configs/small_paper_top_quartile.yaml \
  --universe kabu_native/data/universe/universe_intraday_full.csv
```

#### 対象ウィンドウ

| ソース | ウィンドウ |
|--------|-----------|
| `--reference-run-dir` | `in_sample`（IS） |
| `--extended-oos-json` | Phase38 `extended_oos_validation.json` の各 `run_dir` |
| `--window-run` | 追加（`oos_latest=...` 等） |

April OOS・March（データあれば）・May forward は Phase38 OOS replay を再利用。trade 0 のウィンドウは summary に空行。

#### ウィンドウ別比較（`top_quartile_oos_summary.csv`）

full book PF / quality≥0.55 PF / quality≥0.42 PF / gate accepted 件数・PF / reject 内訳 / symbols / concentration / worst day / max consecutive losers

#### Combined 判定（`move_to_small_paper_candidate`）

| ゲート | 閾値 |
|--------|------|
| combined IS+OOS trades | ≥ 100（`combined_min_trades`） |
| PF | ≥ 1.20 |
| avg_pnl | &gt; 0 |
| symbols coverage | ≥ 70% |
| concentration | &lt; 35% |
| max_concurrent | ≤ 3 |
| risk_clustering | acceptable |
| OOS deterioration | IS gate PF 比 ≤ 20%（`oos_deterioration_max_pct`） |

#### Sample-size gate の意味

IS 単体では top quartile の件数が少ないのは **品質閾値の性質上自然**。Phase40 は OOS を足して **結合 100 件** と **OOS 劣化** を同時に見る — live/paper 前の定量ゲート。

#### 出力

- `top_quartile_oos_validation.json`
- `top_quartile_oos_trades.csv` / `top_quartile_oos_rejects.csv`（`window_id` 列付き）
- `top_quartile_oos_summary.csv`

### Phase 41: Data Accumulation / Latest OOS Window Fix

Phase40 で **trades&lt;100**・**may_late end&lt;start バグ**・**oos_latest 未反映**・**March no_data 不明確** を解消。ロジック変更なし — データ棚卸し・ウィンドウ正規化・latest replay・Phase40 再評価（結合時は trade dedupe）。

```bash
python kabu_native/scripts/run_phase41_data_oos.py \
  --reference-run-dir kabu_native/results/research/logic_lab/20260517/run_225513 \
  --reuse-run oos_april=kabu_native/results/research/logic_lab/phase38_oos/20260518/oos_april \
  --run-latest-replay \
  --revalidate-phase40 \
  --universe kabu_native/data/universe/universe_intraday_full.csv
```

#### OOS ウィンドウ状態（`oos_data_availability.py`）

| status | 意味 |
|--------|------|
| `valid_window` | `start`≤`end` かつ on-disk 営業日あり → replay 可 |
| `no_data` | データなし（例: March 0 日、may_late は 2026-05-16 以降未蓄積） |

`oos_may_late`: latest&lt;2026-05-16 のとき **invalid range を作らず** `no_data` + reason。  
`oos_latest`: 全 root から営業日を union し直近 N 日（既定 10）。

#### 出力

| ファイル | 内容 |
|----------|------|
| `data_availability_for_oos.json` | `data/intraday_1m`・`kabu_native/data/intraday_1m`・`push_jsonl` 棚卸し |
| `latest_oos_window.json` | 全ウィンドウ status / trading_days / run_dir |
| `phase40_top_quartile_oos/` | Phase40 再実行結果（deduped combined） |

#### Sample-size gate

結合 IS+OOS は **重複 window の同一 trade を dedupe** してから `combined_min_trades` を判定。新規営業日データ（May16+ 等）の蓄積がサンプル増の主経路。

### Phase 43: Small Paper Gate Failure Diagnosis

`move_to_small_paper_candidate=false` のとき **未達ゲート一覧・定量化・分類**（`small_paper_gate_diagnosis.py`）。ロジック/閾値変更なし。

```bash
python kabu_native/scripts/run_phase43_gate_diagnosis.py \
  --validation-json kabu_native/results/research/logic_lab/phase41_data_oos/phase40_top_quartile_oos/top_quartile_oos_validation.json
```

| classification | 意味 |
|----------------|------|
| `implementation_issue` | 集計バグ（例: peak concurrent の同一秒順序） |
| `real_risk` | pilot 延期すべきリスク |
| `data_gap` | データ蓄積継続 |

Phase43 集計修正: `_peak_concurrent` は exit→entry 順、`exposure_gate` スロット解放は `exit>=entry`。

### Phase 21: `G6_VOLUME_DELTA` 有効性診断

**閾値変更・G6 緩和・削除はしない。** 出来高増確認が alpha か ENTRY 阻害かを統計で判断。

**G6 が見ているもの（`g6_diagnostic_report.json` → `g6_definition`）**

| 項目 | 意味 |
|------|------|
| `volume_delta_30s` | PUSH 累積出来高の直近 30 秒差分合計 |
| `minute_trading_value` | 閾値スケール用（リプレイは 1 分足 TV） |
| `threshold` | `max(5000, minute_tv × 0.001 × tier係数)` |
| `volume_ratio` | `volume_delta_30s / threshold` |
| `previous_window_volume` | 過去 30 分の 30 秒 delta の p75（ベンチマーク） |
| reject 内訳 | `missing` / `zero` / `below_threshold` |

**G5×G6（`g5_g6_intersection.csv`）**

| 象限 | 意味 |
|------|------|
| `g5_pass_g6_reject` | 高値ブレイク済みだが出来高不足 |
| `g5_reject_g6_pass` | 出来高のみ満たし高値未更新 |
| `g5_g6_both_pass` | 両ゲート通過（候補・ENTRY の母集団に近い） |
| `g5_g6_both_reject` | 両方弾かれ |

**判断基準**

| G6 が有効 (alpha) | G6 が過剰 |
|-------------------|-----------|
| `g6_pass_pf` > 1、pass 側 BF 率低 | `g6_rejected_mfe_rate` ≥ 25% |
| pass forward MFE > reject + 5pt | `g6_pass_rate` が極端に低い |
| MAE・breakout 継続が pass 側で改善 | PF 改善なしで reject 後に伸び多い |

**ボトルネックの見分け:** reject 件数だけでなく `g5_pass_g6_reject` vs `g5_reject_g6_pass` を比較。Phase20 時点では G5 reject が多いが、G5 pass 後は G6 が主フィルタになりうる。

### Phase 20: `G5_ROLLING_HIGH` 有効性診断

**閾値変更・G5 緩和・削除はしない。** 統計のみで「alpha フィルタ」か「過剰フィルタ」かを判断する。

**ゲート定義:** `CurrentPrice` ≤ 直近 5 分 rolling high（PUSH 履歴）→ `G5_ROLLING_HIGH`

| 指標 | G5 が有効（alpha）の目安 | G5 が過剰の目安 |
|------|---------------------------|------------------|
| 実トレード | `g5_pass_pf` > 1、pass 側 MFE/MAE 良好 | PF 改善なし |
| forward（reject 側） | `g5_rejected_mfe_rate` 低い | **≥25%** が reject 後も MFE+0.3% |
| 高値更新 | reject 後の `rejected_then_high_update` 少 | 率高い |
| ENTRY | `trades_after_g5` が妥当 | pass_rate 極端に低く機会損失 |

**alpha フィルタ vs 過剰フィルタ**

- **alpha フィルタ:** G5 pass 後だけ PF・breakout 継続・MFE が改善し、reject 後の forward 伸びは少ない
- **過剰フィルタ:** reject 後も `g5_rejected_but_extended.csv` で MFE+0.3% / 高値更新が多く、`g5_possible_overfilter=true` かつ pass トレードの PF は悪くない

**G5 を変更してよい条件（人手判断）**

1. 複数プロファイルで `g5_is_alpha_positive=true` が安定
2. `g5_possible_overfilter=false`、または overfilter でも pass PF が明確に悪い
3. baseline / continuation_v1 で次ボトルネック（G3 等）診断済み

### Phase 19: G7 リプレイ定義（セッション累積 TV）

**本番 G7 閾値 5億円は変更しない。** リプレイ合成 PUSH の `TradingValue` のみ修正。

| 板フィールド | 意味 | G7 での利用 |
|--------------|------|-------------|
| `TradingValue` | セッション開始からの `volume×close` 累積 | **G7_TRADING_VALUE**（本番想定） |
| `MinuteTradingValue` | 当該 1 分足の `volume×close` | 診断のみ（`G7B_*` 相当の集計） |
| `IncrementalTradingValue` | 10 分割 PUSH の増分 TV | Phase18 比較・`pass_rate_old` |

実装: `src/kabu_signal_replay.py` の `push_messages_from_yahoo_df(..., trading_value_mode="session_cumulative")`（デフォルト）。

G6（30秒出来高）は **セッション累積 TV とスケールが合わない** ため、合成板に `MinuteTradingValue` がある場合のみ `volume_threshold` に分足 TV を使う（`src/kabu_signal_engine.py`）。本番板に `MinuteTradingValue` が無い場合は従来どおり `TradingValue`。

**評価の見方**

- `g7_pass_rate`（新）が `g7_pass_rate_old` より大幅に高い → 定義修正が効いている
- `g7_reject_count` が eval の ~99% から下がる → G7 異常支配の解消
- `top_reject_reason` が **G5 / G3** に移る → 次のボトルネック分析へ
- `entries_per_day`（baseline / continuation_v1）が Phase18 比で増えるか

### Phase 18: `G7_TRADING_VALUE` 診断の読み方（履歴）

ENTRY 不足の主因が **本当の売買代金不足** か、**リプレイデータの定義ずれ** かを切り分ける。

| 出力 | 用途 |
|------|------|
| `rejects_by_profile.csv` | G7 行に `missing_count` / `zero_count` / `below_threshold_count` / p50–p90 / `threshold` |
| `g7_trading_value_diagnostic.json` | プロファイル別の TV 分布・CSV 比較・ヒューリスティック |

**ゲート定義（変更していない）**

- `G7_TRADING_VALUE`: 板の `TradingValue` < **5億円**（`MIN_TRADING_VALUE`）
- 本番 kabu 板は **セッション累積** 売買代金であることが多い
- Phase18 時点のリプレイは **増分 TV** を `TradingValue` に載せていた（Phase19 で修正済み）

**典型的な結論パターン**

| パターン | 目安 | 意味 |
|----------|------|------|
| データ品質 | `missing_count`+`unknown_count` が G7 reject の 20%超 | 欠損・不明が主因 → CSV/板マッピングを確認 |
| 閾値・定義ずれ | `below_threshold_count` が 80%超、board TV p90 ≪ 5億、CSV 1分足 TV は相対的に大 | **合成増分 TV** を **セッション閾値** で弾いている可能性大（Phase18 時点の主因候補） |
| 本当に薄い銘柄 | CSV `bar_volume×close` の p90 も閾値未満 | ユニバース除外・銘柄フィルタを検討（閾値変更前に） |

`top_reject_is_data_quality_issue=true` → 欠損・不明優勢。  
`possible_threshold_too_strict=true` → **データはあるがゲート定義がリプレイと不整合** の疑い（G7 を外す判断はまだしない）。

**次に触るゲート（G7以外）**

`rejects_by_profile.csv` で G7 以外の上位（例: `G5_ROLLING_HIGH`, `G3_VWAP_DIST`）を確認してから、ロジックプロファイル比較を行う。

## 採用基準（paper_trade 再開の目安）

自動採用はしない。`profile_summary` の `adoption_review` は **ヒューリスティック警告** のみ。

人手で次を確認する。

| チェック | 合格の目安 |
|----------|------------|
| 候補数 | `candidates_per_day` が極端に少ない（<1）でない |
| ENTRY 数 | `entries_per_day` が検証可能な水準（目安 ≥0.3/日・銘柄ユニバース依存） |
| PF | baseline 比 **悪化のみ** のプロファイルは採用しない |
| MFE | `avg_mfe_pct` / MFE 到達率が baseline 比改善傾向 |
| max_loss | baseline 比 **大幅悪化** なし |
| 依存 | `symbols_with_trades` が 1 銘柄に偏っていない |
| 日付 | `day_summary` で特定日だけ異常に良い/悪いパターンがない |

**trade 数だけ減って PF が上がった** プロファイルは `trade_count_drop_without_pf_gain` 警告対象。

## paper_trade / Discord 実運用へ進める条件

次を **すべて満たす** ときのみ、shadow paper_trade や Discord 仮想売買通知の再開を検討する。

1. Logic Lab を **universe 全 passed 銘柄 × 複数営業日** で実行済み
2. 採用プロファイルが baseline 比で **PF・MFE・max_loss** を悪化させていない
3. ENTRY 減少の主因が `reject_reasons` から説明できる（チューニング前に診断済み）
4. `check_shadow_safety.py` 合格（`order_enabled=false` 維持）
5. 日曜・時間外は **`run_replay.py --discord-notify`** で通知経路のみ別途確認

## 関連

- [replay.md](replay.md) — バッチ replay
- [shadow.md](shadow.md) — realtime shadow（別系統）
- [market_session_control.md](market_session_control.md) — ENTRY 時間枠（構造ルール）
