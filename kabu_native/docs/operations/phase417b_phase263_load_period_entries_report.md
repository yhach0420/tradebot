# Phase417B — Phase263 load_period_entries Bug Audit / Fix

Generated: 2026-06-16T23:11:29+09:00

## 必須回答

1. **27件になった直接原因**: load_period_entries required entry_price>0; Phase399/Baseline rows lack entry_price except 20260616 structural_trades
2. **バグか仕様か**: バグ（入力正規化不足）
3. **修正内容**: `load_period_entries()` に `resolve_entry_price()` / `build_structural_entry_price_index()` を追加。 `close_time→exit_time`、day列欠落時の entry_time 日付抽出、 close_price+pnl 逆算、structural_trades.csv ルックアップで entry_price を補完。
4. **修正後 base_entry_count**: 681
5. **修正後 dynamic_stop_candidate**: False
6. **修正後 best_policy_at_1p5m**: dynamic_stop_risk_0p25
7. **Phase263採用判断は変わるか**: Baseline B は `best_policy_at_1p5m=dynamic_stop_risk_0p25`・`dynamic_stop_candidate=false` のまま。 ただしサンプル信頼性が 27→681 に改善。
8. **Phase416のどの結論を修正すべきか**: `phase263_equity_dynamic_stop_shadow` Baseline B の `base_entry_count` / `dynamic_stop_candidate` / `best_policy_at_1p5m` を再評価。

## Audit checklist

- period_days: 11 days — 20260529, 20260601, 20260602, 20260603, 20260608, 20260609, 20260610, 20260611, 20260612, 20260615, 20260616
- trades_by_day days: True
- trade_counts_by_day: {'20260529': 66, '20260601': 81, '20260602': 76, '20260603': 63, '20260608': 60, '20260609': 68, '20260610': 77, '20260611': 55, '20260612': 52, '20260615': 56, '20260616': 27}
- Baseline A legacy→fixed: 774 → 1529
- Baseline B legacy→fixed: 27 → 681
- structural_price_index_size: 3172

## Verdict (Baseline B recomputed)

- dynamic_stop_candidate: False
- best_policy_at_1p5m: dynamic_stop_risk_0p25
- best_policy_at_5m: dynamic_stop_risk_0p25
- recommendation: Some risk budgets rarely tighten stops (capped at 1.2%): 0.5%, 0.75%, 1.0%. At 2M yen, realized max loss ratio can exceed 2× the configured risk budget on this sample.
