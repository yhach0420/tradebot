# Phase424 — Phase273/274 Equity Curve Consistency Audit

Generated: 2026-06-17T20:15:54+09:00
Verdict: **bug_fixed** (bug)

## Root cause

Phase273/274 used load_period_trades (raw structural_trades.csv across all sessions) without Phase413 no_overlap_replace collapse or Phase423 canonical historical baseline. Legacy stream had 2518 trades vs canonical 741.

## 必須回答

1. **履歴ソース**: legacy=`load_period_trades` (raw structural); fix=`load_canonical_live_config_trades` (Phase423 baseline B + forward collapse)
2. **ズレ直接原因**: raw structural 2528 trades（overlap 連鎖含む）を全期間再シミュ → Phase423 の 681+forward と不一致
3. **バグか仕様か**: **バグ**（canonical baseline 導入後も Phase273/274 が旧 loader のまま）
4. **修正内容**: `load_canonical_live_config_trades` 追加、Phase273/274 がこれを使用
5. **修正後 20260616 equity**: 1641767.98
6. **修正後 20260617 AM equity**: 1668067.98
7. **修正後 20260617 PM equity**: 1645767.98
8. **Phase273 recommendation**: scale_candidate_3000k
9. **Phase274 adoption verdict**: adopt
10. **今後の daily forward 基準**: Phase423 canonical baseline + 当日 forward collapsed structural

## Before vs After (1500k)

| | legacy Phase273 | after fix |
|---|-----------------|-----------|
| final equity | 1477030.0 | 1645767.98 |
| 20260616 end | 1482530.0 | 1641767.98 |
| accepted | 1844 | 730 |
