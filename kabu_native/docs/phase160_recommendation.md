# Phase 160: fade exit review recommendation

**Verdict:** `mixed_result`

## Summary (all sessions, fade exits only)

- Fade exits analyzed: 460
- Post-exit **reacceleration** (new high / MFE @120s): 38.04%
- Post-exit **range-hold** (価格が exit 付近で推移): 42.39%
- Post-exit **breakdown** (安値更新・回復なし): 19.57%

## Interpretation

- 約4割が exit 後に再加速 → fade が早すぎる候補が多いが、breakdown は約2割のみ。
- **横ばい継続 (range_hold) が最大クラスタ** → 「全部 fade 禁止」より **崩れ確認付き継続** が筋が良い。

## Policy what-if (fade trades, tick replay)

- **A 現行**: PF 1.2693, avg 0.0158%, total 7.2598%
- **B fade 無効（他 exit のみ）**: PF 1.3462, avg 0.0175%, improved 49 / worsened 22
- 現行より PF 改善はあるが、無条件 hold は max_loss 悪化リスクあり → breakdown ゲート設計を優先。

## Cap5-only fade (Phase158 subset)

- Fade count: 18
- Reacceleration: 55.56%
- Avg exit PnL: 0.0263% vs avg best+120s: 0.4789%
- cap5 層では fade 後の取りこぼしがより顕著 → cap 増加前に exit 条件の見直しが必要。

## Breakdown rule candidates (top precision)

- `R6_no_take_and_negative`: precision 0.3803, recall 0.3, false-reaccel hold 0.6197
- `R4_momentum_below_015`: precision 0.3438, recall 0.2444, false-reaccel hold 0.6562
- `R9_pre_exit_range_tight`: precision 0.3333, recall 0.2667, false-reaccel hold 0.6667
- 単独ルールの precision は概ね 0.33–0.38。複合条件の shadow 検証が次ステップ。

## Notes

- fade_count=460 reacceleration_rate=38.0%

## Next step (shadow only)

1. Phase161: breakdown 複合ルール（R6+R4 等）の shadow replay
2. fade 遅延（2-tick 確認）+ take 到達後のみ fade を別 CSV で比較
3. 本番 YAML / entry / exit は変更しない

## Constraints

- Review only; `order_enabled=false`, `paper_only=true`.
