# Phase495 — New Feature Guard Replay

**Verdict:** `needs_forward_shadow`  
**Period:** 20260529 — 20260622 | Baseline: PnL +244,963 / PF 1.62 / maxDD 53,899

## 必須回答

| # | 質問 | 回答 |
|---|------|------|
| 1 | 最良guard | **D: `MST_near_day_high_flag == 1` reject** |
| 2 | delta PnL | **+106,901** (351,864 vs 244,963) |
| 3 | PF改善 | **+0.77** (1.62 → 2.39) |
| 4 | maxDD変化 | **-17,399** (53,899 → 36,500、改善) |
| 5 | blocked winners | **32** |
| 6 | blocked losers | **28** |
| 7 | falling_knife削減 | **2** (14→12) |
| 8 | high_price_extension削減 | **8** (11→3) |
| 9 | 6976影響 | blocked PnL **-20,000**（勝ちトレード喪失） |
| 10 | 6522影響 | **0**（replay pool に 6522 accepted なし） |
| 11 | AMを壊すか | **Yes** — blocked AM PnL -32,201（AM利益を削る） |
| 12 | PMを改善するか | **Yes** — blocked PM PnL +10,400（PM損失トレードを除去） |
| 13 | 6/22依存か | **No** — exclude_6/22 でも delta +106,901（6/22は replay pool に含まれず） |
| 14 | overfit risk | **low** — LOO 13/13日すべて delta>0 |
| 15 | Runtime候補 | **No** — blocked winners 32件（Phase493と同型の winner FP 問題） |
| 16 | Shadow候補 | **Yes** — `MST_near_day_high_flag` + `EXH_chase_intensity` を観測ログに追加 |
| 17 | 次アクション | Forward-shadow **D** on live entries → Phase496 counterfactual with winner-FP cap |

## Guard ランキング（delta PnL 順）

| Scenario | delta PnL | PF | maxDD | blocked W/L | FK↓ | HPE↓ |
|----------|-----------|-----|-------|-------------|-----|------|
| **D_MST_near_day_high** | **+106,901** | 2.39 | 36,500 | 32/28 | 2 | **8** |
| E_EXH_chase_top20 | +59,901 | 2.03 | 35,499 | 24/25 | 3 | 2 |
| H_MST+EXH | +28,200 | 1.82 | 39,199 | 16/14 | 2 | 1 |
| I_conservative_E_D | +28,200 | 1.82 | 39,199 | 16/14 | 2 | 1 |
| B_RSY_r5_top20 | +3,500 | 1.74 | 45,099 | 25/21 | 1 | 3 |
| F_PBQ+MST | +2,400 | 1.64 | 58,699 | 2/4 | 2 | 0 |
| G_PBQ+RSY_r5 | 0 | 1.62 | 53,899 | 0/0 | 0 | 0 |
| C_RSY_r10_top20 | -23,901 | 1.62 | 48,699 | 26/15 | 0 | 0 |
| **A_PBQ_negative_r5** | **-184,303** | 1.20 | 86,900 | **56/27** | 12 | 0 |

**重要:** Phase494 最強特徴 `PBQ_negative_r5_board_midhigh` を単独guardにすると **大幅悪化**（winner 56件ブロック）。合成特徴の分離力 ≠ replay edge。

## Robustness（最良 D）

| Test | delta vs baseline |
|------|-------------------|
| LOO by day (13日) | 全て **+69k〜+117k** |
| exclude_6976 | +86,901 |
| exclude_6522 | +106,901 |
| exclude_top_symbol | +107,900 |
| AM_only | +117,301 |
| PM_only | **-2,200** |
| exclude_6/22 | +106,901 |

**解釈:** D guard はグローバルに replay edge あり。ただし PM-only サブセットでは edge 消滅 → **PM特化ではなく AM 側 winner 犠牲が大きい**。

## 成果物

- `results/reports/phase495_new_feature_guard_replay.csv`
- `results/reports/phase495_new_feature_guard_symbol_day.csv`
- `results/reports/phase495_new_feature_guard_robustness.csv`
- `results/reports/phase495_summary.json`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase495_new_feature_guard_replay.py --parallel --max-workers 2
```
