# Phase499 — Post Entry Behavior Audit

**Verdict:** `post_entry_feature_found`  
**Period:** 20260529 — 20260622 | PBv2 accepted **286件** (enriched **281件**, missing price **1.75%**)

## 目的

Phase483〜498で ENTRY 時点の特徴量だけでは勝ちを壊さず負けだけを十分に分離できないことが判明。  
本フェーズでは **ENTRY後 30〜180秒** の初動挙動を監査し、「入った直後に失敗が見えているか」を検証する。

**制約:** Runtime / YAML / Entry / Exit / Order / Discord 変更なし（リサーチのみ）

## Cohort 定義

| Group | 定義 | n |
|-------|------|---|
| winner | `trailing_mfe_exit` OR `pnl_yen_100 > 0` | 164 |
| loser | `stop_hit` / `no_progress_exit` OR `pnl_yen_100 < 0` | 107 |

**Baseline:** PnL **+244,963** / PF **1.62** / maxDD **53,899** / avg hold **1,230s**

## 必須回答

| # | 回答 |
|---|------|
| 1 post-entryで最も分離する特徴量 | **`high_update_after_entry_count_180s`** (d=-1.07, KS=0.45, MI=0.15) — loser は高値更新が少ない |
| 2 30秒時点で負けは見えるか | **部分的** — E1 (pnl<-0.2%): W=18.3% vs L=30.8%; loser median pnl **-0.10%** vs winner **0%** |
| 3 60秒時点で負けは見えるか | **はい（最強）** — E2 (MFE<0.1%): W=31.7% vs L=67.3%; loser median MFE **0%** vs winner **0.19%** |
| 4 120秒時点で負けは見えるか | **はい** — E3 stall: W=28.1% vs L=63.6%; E4 no reclaim: W=14.6% vs L=46.7% |
| 5 最良early failure pattern | **E2** (60s MFE<0.1%) — W/L rate gap **+35.6pp** |
| 6 最良exit overlay | **A_baseline** — 全 overlay が delta PnL マイナス |
| 7 delta PnL | **0** (baseline維持) |
| 8 PF改善 | **0** (1.62維持) |
| 9 maxDD変化 | **0** |
| 10 early_exit件数 | **0** |
| 11 cut winners | **0** |
| 12 saved losers | **0** |
| 13 6976影響 | **0** (baseline) |
| 14 6/22依存 | **No** |
| 15 AMを壊すか | **No** (overlay未採用) |
| 16 PMを改善するか | **No** (overlay未採用) |
| 17 overfit risk | **low** — 上位特徴量 LOO 13日すべて stable (loo_robust=True) |
| 18 Runtime候補 | **No** — exit overlay は全て winner 過剰カット |
| 19 Shadow候補 | **Yes** — 30/60/120s checkpoint (mfe, pnl, reclaim, high_update_count) をログ |
| 20 次アクション | Shadow-log 継続; cut_winners < 10 になる overlay が出るまで exit 変更なし |

## Part A — Post Entry Features

各 trade について entry 後 **30 / 60 / 120 / 180秒** で以下を計算:

- `pnl_pct_at_t`, `mfe_pct_at_t`, `mae_pct_at_t`, `price_change_at_t`
- `board_imbalance_change_at_t`, `vwap_dev_change_at_t`
- `high_update_after_entry_count`, `new_low_after_entry_flag`
- `reclaim_entry_price_flag`, `failed_reclaim_flag`

## Part B — Winner / Loser 比較 (上位特徴量)

| Rank | Feature | W median | L median | \|d\| | LOO robust |
|------|---------|----------|----------|-------|------------|
| 1 | high_update_after_entry_count_180s | 3.0 | 1.0 | **1.07** | Yes |
| 2 | pnl_pct_at_180s | +0.20% | -0.27% | 1.02 | Yes |
| 4 | pnl_pct_at_120s | +0.09% | -0.30% | 0.99 | Yes |
| 8 | mfe_pct_at_120s | +0.35% | +0.04% | 0.83 | Yes |
| 9 | high_update_after_entry_count_60s | 2.0 | 0.0 | 0.83 | Yes |
| 12 | E3_120s_stall | 0% | 100% | 0.76 | Yes |
| 13 | **E2_60s_mfe_lt_01** | 0% | 100% | **0.76** | Yes |
| 29 | pnl_pct_at_30s | 0% | -0.10% | 0.45 | Yes |

**解釈:** loser は entry 後に高値更新が少なく、60秒時点で MFE が伸びない（stall）。30秒では差が小さく、60〜180秒で分離が強まる。

## Part C — Early Failure Patterns

| Pattern | 定義 | W rate | L rate | Gap |
|---------|------|--------|--------|-----|
| **E1** | 30s pnl < -0.2% | 18.3% | 30.8% | +12.5pp |
| **E2** | 60s MFE < 0.1% | 31.7% | **67.3%** | **+35.6pp** |
| E3 | 120s MFE<0.2% AND pnl<0 | 28.1% | 63.6% | +35.5pp |
| E4 | 120s entry価格未回復 | 14.6% | 46.7% | +32.1pp |
| E5 | 60s new_low | 51.8% | 80.4% | +28.6pp |
| E6 | board_imbalance_change_60s < -X | 26.8% | 8.4% | -18.4pp (逆) |
| E7 | vwap_dev_change_60s < -X | 38.4% | 53.3% | +14.9pp |

E6 は loser 率が低く逆方向。E2 が最も実用的な分離指標。

## Part D — Counterfactual Exit Overlay

Entry は変更せず、overlay exit のみ検証。**全シナリオが baseline を下回る。**

| Scenario | delta PnL | PF | early_exit | cut_W | saved_L | avg_hold |
|----------|-----------|-----|------------|-------|---------|----------|
| **A_baseline** | **0** | **1.62** | 0 | 0 | 0 | 1,230s |
| G1_E6_board_drop_60s | -54,500 | 1.52 | 53 | 26 | 9 | 474s |
| E_E4_120s_no_reclaim | -66,001 | 1.54 | 79 | 36 | 35 | 489s |
| D_E3_120s_stall | -93,130 | 1.42 | 111 | 51 | 48 | 457s |
| G2_E7_vwap_drop_60s | -98,099 | 1.48 | 110 | 48 | 43 | 378s |
| C_E2_60s_mfe_lt_01 | -136,399 | 1.34 | 114 | **52** | 50 | 431s |
| B_E1_30s_pnl_lt_neg02 | -168,698 | 1.22 | 79 | 41 | 33 | 470s |
| F_E5_60s_new_low | **-233,121** | 1.06 | 187 | **97** | 75 | 284s |

**核心問題:** E2 は loser の 67% を捕捉できるが、winner の 32% も誤爆（52件 cut）。saved_losers ≈ cut_winners で net PnL 悪化。

### Symbol / Session 影響 (worst overlay F_E5 参考)

| Slice | delta (F_E5) |
|-------|----------------|
| 6976 | -157,500 |
| 4062 | -6,001 |
| 6522 | 0 |
| 6/22 | 0 |
| AM | -126,419 |
| PM | -106,701 |

Overlay 採用時は AM/PM 両方で大幅悪化。6/22・6976 依存ではないが、広範な winner カットが原因。

## Part E — Robustness

- **Feature LOO:** 上位 40 特徴量中 38 件が `loo_robust=True` (13日すべて \|d\| > 0.25)
- **Overlay robustness:** best = baseline のため `phase499_post_entry_robustness.csv` は空（overlay 改善なし）
- **6976 / 6/22 依存:** overlay 未採用 → 該当なし
- **Overfit risk:** 特徴量分離は日次安定; exit overlay は過剰カットで実用不可

## 判定根拠

| 観点 | 結論 |
|------|------|
| 特徴量分離 | **あり** — 60〜180s で \|d\| > 0.75 |
| Exit overlay | **なし** — 全 overlay delta < 0 |
| Verdict | **`post_entry_feature_found`** (not `post_entry_exit_candidate`) |

ENTRY Guard ではなく **初動監査**。実装候補は shadow logging のみ。

## 成果物

- `results/reports/phase499_post_entry_behavior.csv` (281 trades × post-entry features)
- `results/reports/phase499_post_entry_feature_ranking.csv`
- `results/reports/phase499_post_entry_exit_overlay.csv`
- `results/reports/phase499_post_entry_robustness.csv`
- `results/reports/phase499_summary.json`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase499_post_entry_behavior_audit.py --parallel --max-workers 2
```
