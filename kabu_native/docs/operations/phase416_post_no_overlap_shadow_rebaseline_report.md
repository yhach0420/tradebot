# Phase416 — Post-Phase414 Historical Shadow Rebaseline

## Conclusion (status)

- status: **rebaseline_complete**

## 必須回答

- **1. Phase414後の基準履歴はどれか**: Baseline B（`phase413_no_overlap_replace_backfill`）を新基準として扱う。
- **2. Phase409 Boundary は改善するか悪化するか**: eligible 402→375, shadowΔPnL 128490.32→112500.09, shadow PF 1.2443→1.3047, shadow maxDD 73750.58→71090.62.
- **3. Phase273/274 の150万円資産推移はどう変わるか**: Phase273 recommended=scale_candidate_3000k→scale_candidate_3000k. Phase274 adoption=adopt→reject.
- **4. Phase262/263/266 の採用判断は変わるか**: Phase263 best_policy_at_1p5m=dynamic_stop_risk_0p25→dynamic_stop_risk_0p25. Phase262/266 は本 runner では未実装。
- **5. Phase400〜408 Exit研究の順位は変わるか**: 本 runner では未実装（再計算が必要）。
- **6. 以前の採用候補で無効化すべきものはあるか**: Baseline B 前提で再評価が必要（trade_count 母集団が大幅に変化）。
- **7. 明日以降見るべきshadowはどれか**: Phase409 / Phase273 / Phase274 / Phase263 を継続監視（Phase262/255系は別途 rebaseline 実装が必要）。

## Baselines

- A: phase399_position_cap_history_plus_20260616 (trades=1529)
- B: phase413_no_overlap_replace_backfill (trades=681)

## Module coverage

- baseline_trade_metrics: ok
- phase255_sector_heat_forward_shadow: insufficient_inputs (not implemented in Phase416 runner)
- phase256_sector_heat_forward_shadow: insufficient_inputs (not implemented in Phase416 runner)
- phase262_risk_sizing_shadow: insufficient_inputs (not implemented in Phase416 runner)
- phase263_equity_dynamic_stop_shadow: ok
- phase266_equity_dynamic_stop_shadow: insufficient_inputs (not implemented in Phase416 runner)
- phase273_live_config_forward_shadow: ok
- phase274_live_config_auto_transition_shadow: ok
- phase400_to_408_exit_research_rank: insufficient_inputs (not implemented in Phase416 runner)
- phase409_boundary_shadow: ok
