# Phase498 — Chase Quality Audit

**Verdict:** `chase_quality_guard_candidate`  
**Period:** 20260529 — 20260622 | PBv2 accepted **286件**

## Chase Cohort 定義

`r10 >= 1.0` **OR** `r10 >= 70th percentile` (top30%, threshold **0.979**)

| Group | 定義 | n |
|-------|------|---|
| W_chase | chase かつ (trailing_mfe OR pnl>0) | 37 |
| L_chase | chase かつ (stop_hit / no_progress / pnl<0) | 24 |
| non_chase | reference | 223 |

## 必須回答

| # | 回答 |
|---|------|
| 1 chase cohort件数 | **63** (W=37, L=24) |
| 2 chase PnL / PF | **+14,082 / 1.20** |
| 3 non-chase PnL / PF | **+230,881 / 1.71** |
| 4 勝つchase | board_imbalance やや高、chase_near_high_exhaustion **低** (0.13)、PBQ_dip 低、inverse_day_high 低 |
| 5 負けるchase | chase_near_high_exhaustion **高** (0.38)、RSY_r5 高、inverse_day_high 高、r10 やや高 (1.75) |
| 6 最強特徴 | **既存 `board_imbalance`** (d=-0.48) — loser は board 弱い |
| 7 新規>既存? | **No** — 新規最強 `chase_near_high_exhaustion` (d=0.31) |
| 8 最良guard | **C: chase_near_high_exhaustion top20** |
| 9 delta PnL | **+54,100** |
| 10 blocked winners | **25** |
| 11 blocked losers | **25** |
| 12 6976影響 | **0** |
| 13 6/22依存 | **No** |
| 14 AMを壊すか | **Yes** — blocked AM -57,401 |
| 15 PMを改善するか | **Yes** — blocked PM +3,300 |
| 16 overfit risk | **moderate** (winner FP 25) |
| 17 Runtime候補 | **No** |
| 18 Shadow候補 | **Yes** — chase_near_high_exhaustion, chase_decay_score |
| 19 次アクション | G保守版 (C+A) shadow: 8W/15L blocked, delta +53k |

## W_chase vs L_chase 核心差分

| Feature | W median | L median | \|d\| |
|---------|----------|----------|-------|
| board_imbalance | 0.50 | 0.47 | 0.48 |
| chase_near_high_exhaustion | **0.13** | **0.38** | 0.31 |
| RSY_r5_minus_symbol_median | 0.17 | 0.57 | 0.24 |
| EXH_inverse_day_high_dist | 0.33 | 0.55 | 0.24 |
| r10 | 1.53 | 1.75 | 0.23 |

**インサイト:** chase 内 loser は「高 r10 + 高値圏 exhaustion」の合成。単独 r10 では不十分。

## Counterfactual Guards

| Guard | delta PnL | PF | blocked W/L |
|-------|-----------|-----|-------------|
| **C_near_high_exhaustion_top20** | **+54,100** | 2.00 | 25/25 |
| G_conservative C+A | +53,301 | 1.91 | **8/15** |
| A_chase_decay_top20 | +6,600 | 1.79 | 26/22 |
| B_followthrough_bottom20 | -20,400 | 1.63 | 22/16 |
| F_vwap_confirmation_bottom20 | -112,203 | 1.44 | 47/24 |

**推奨 Shadow:** Guard **G** (C+A 2条件) — delta ほぼ同等で winner FP 8件。

## Robustness (Guard C)

| Test | delta |
|------|-------|
| LOO 13日 | 全て **+32k〜+59k** |
| exclude_6976 | +54,100 |
| exclude_6/22 | +54,100 |
| exclude_top_symbol | +37,599 |
| AM_only | +57,401 |
| PM_only | **-3,300** |

## 成果物

- `results/reports/phase498_chase_quality_audit.csv`
- `results/reports/phase498_chase_feature_ranking.csv`
- `results/reports/phase498_chase_counterfactual.csv`
- `results/reports/phase498_chase_robustness.csv`
- `results/reports/phase498_summary.json`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase498_chase_quality_audit.py --parallel --max-workers 2
```
