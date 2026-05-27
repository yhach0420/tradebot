# Phase 161: fade shadow policy recommendation

**Verdict:** `breakdown_confirmed_promising`

## Scenario comparison (all trades, cap3 sessions)

| Scenario | PF | avg PnL | fade exits | avoided fade | reaccel saved | worsened |
|----------|-----|---------|------------|--------------|---------------|----------|
| A_current | 0.7529 | -0.0138 | 517 | 162 | 5 | 294 |
| B_no_fade | 1.0649 | 0.0088 | 0 | 460 | 101 | 385 |
| C_two_tick_delay | 0.7529 | -0.0138 | 516 | 163 | 5 | 294 |
| D_breakdown_confirmed | 1.372 | 0.0373 | 115 | 396 | 89 | 351 |
| E_range_hold_protect | 1.5859 | 0.0328 | 414 | 222 | 45 | 291 |
| F_take_reached_only | 1.038 | 0.0043 | 111 | 392 | 84 | 367 |
| G_hybrid | 1.7747 | 0.043 | 403 | 228 | 51 | 285 |

## Key findings

- **C 2-tick delay**: ほぼ現行と同じ（fade 516 vs 517）→ 単独では効果なし。
- **D breakdown confirmed**: fade 115件（-78%）、PF 1.37、reaccel saved 89。
- **G hybrid**: PF 1.77（最高）、fade 403、max_loss は現行同等。
- **E range-hold protect**: PF 1.59、fade 414 — G より保守的。
- **B fade 無効**: PF 1.06 だが session_close +517、hold 延長リスク大。
- **F take-only**: PF 1.04、fade 111 — 改善は限定的。

シミュレーションは各トレード **actual_close + 300s** まで（孤立 replay）。

## Notes

- best_non_current=G_hybrid pf=1.7747 current_pf=0.7529
- G_hybrid pf_delta=1.022

## Next step (live shadow)

1. **G hybrid** を `fade_watch` shadow で live 検証（最優先）
2. 次点 **D breakdown confirmed**（シンプル版として A/B）
3. **C 2-tick** は単独採用しない

## Constraints

- Review only; cap=3; `order_enabled=false`; `paper_only=true`.
