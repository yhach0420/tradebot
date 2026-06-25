# Phase494 — New Feature Discovery

Research-only. PBv2 CAP=5 replay, 20260529–20260622, 286 accepted trades.

## 必須回答

### 既存特徴量より強い特徴が見つかったか

**Yes.** 新規 `PBQ_negative_r5_board_midhigh` (|d|=2.24) が既存最強 `r5` (|d|=1.26) を上回る。  
2位新規 `RSY_r5_minus_symbol_median` (|d|=1.35) も `r5` を僅差で上回る。

### Top20 新特徴量

| Rank | Feature | Category | |d| | KS | LOO robust |
|------|---------|----------|-----|-----|------------|
| 1 | PBQ_negative_r5_board_midhigh | Pullback Quality | 2.24 | 0.71 | Yes |
| 2 | RSY_r5_minus_symbol_median | Relative Strength | 1.35 | 0.67 | Yes |
| 3 | RSY_r10_zscore_in_day | Relative Strength | 1.15 | 0.56 | Yes |
| 4 | MST_near_day_high_flag | Market Structure | 1.03 | 0.35 | Yes |
| 5 | PBQ_board_supported_dip | Pullback Quality | 0.88 | 0.71 | Yes |
| 6 | EXH_inverse_day_high_dist | Exhaustion | 0.76 | 0.65 | Yes |
| 7 | MST_vwap_structure_score | Market Structure | 0.67 | 0.42 | Yes |
| 8 | MST_extension_near_high | Market Structure | 0.60 | 0.31 | Yes |
| 9 | EXH_chase_intensity | Exhaustion | 0.54 | 0.42 | Yes |
| 10 | RSY_vwap_dev_z_proxy | Relative Strength | 0.43 | 0.34 | Yes |
| 11 | PBQ_vwap_pullback_gap | Pullback Quality | 0.43 | 0.38 | Yes |
| 12 | RSY_composite_strength_pct | Relative Strength | 0.38 | 0.44 | Yes |
| 13 | RSY_momentum_board_spread | Relative Strength | 0.37 | 0.28 | Yes |
| 14 | EXH_stale_high_vwap | Exhaustion | 0.34 | 0.47 | Yes |
| 15 | TCX_momentum_slope | Trend Context | 0.33 | 0.39 | Yes |
| 16 | RSY_imbalance_excess | Relative Strength | 0.32 | 0.35 | Yes |
| 17 | EXH_rally_decay_r15_r5 | Exhaustion | 0.29 | 0.32 | Yes |
| 18 | EXH_vwap_extension_rate | Exhaustion | 0.21 | 0.47 | Yes |
| 19 | EXH_vwap_board_extension | Exhaustion | 0.08 | 0.34 | No |
| 20 | RSY_strength_index | Relative Strength | 0.07 | 0.36 | No |

### 既存 Top 特徴量との比較

| Rank (global) | Feature | Type | |d| | Direction |
|---------------|---------|------|-----|-----------|
| 1 | PBQ_negative_r5_board_midhigh | **new** | 2.24 | lower_in_loser |
| 2 | RSY_r5_minus_symbol_median | **new** | 1.35 | higher_in_loser |
| 3 | r5 | existing | 1.26 | higher_in_loser |
| 4 | RSY_r10_zscore_in_day | **new** | 1.15 | higher_in_loser |
| 5 | r10 | existing | 1.05 | higher_in_loser |
| 6 | MST_near_day_high_flag | **new** | 1.03 | higher_in_loser |

**解釈:** falling_knife 系は `PBQ_negative_r5_board_midhigh`（r5&lt;0 ∧ board mid/high）が r5 単体より分離が強い。high_price_extension 系は `MST_near_day_high_flag` / `EXH_inverse_day_high_dist` / `EXH_chase_intensity` が既存 `day_high_distance` より上位。late_chase 系は `EXH_rally_decay_r15_r5` が既存 `r15_minus_r5` と同等だが新規カタログ内で再確認。

### Runtime 候補か

**No.** winner 定義（trailing_mfe + session_close 黒字）が **n=3** と極小。LOO は安定だが Phase493 と同様 winner FP リスクが未検証。Runtime 投入は counterfactual ゲート試験後。

### Shadow 候補か

**Yes.** 以下を Shadow 観測フィールドとして推奨:

- `PBQ_negative_r5_board_midhigh` — falling_knife proxy
- `RSY_r5_minus_symbol_median` — 銘柄内相対弱さ
- `MST_near_day_high_flag` + `EXH_chase_intensity` — high_price_extension proxy

## 成果物

- `results/reports/phase494_feature_discovery.csv` — 286 trades × 51 features
- `results/reports/phase494_feature_ranking.csv` — Top20 新特徴量
- `results/reports/phase494_summary.json`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase494_new_feature_discovery.py
```

## Verdict

`new_feature_found` — Shadow 観測追加を推奨。Runtime ゲートは Phase495 counterfactual 待ち。
