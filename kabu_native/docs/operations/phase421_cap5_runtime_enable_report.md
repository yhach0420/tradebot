# Phase421 — CAP5 Runtime Enable (Part A–D)

目的: Phase420 の adoption review を受け、**Runtime / Phase273 / Phase274 の 1500k band を CAP5 に反映**し、preflight 要件を満たすことを確認する。

制約: Entry/Exit/Boundary/Order/Discord のロジック変更は行わない（CAPのみ）。

## 反映内容

- **Runtime**: `max_concurrent_positions: 3 → 5`
- **Phase273**: `live_start_candidate_1500k` の `cap: 3 → 5`
- **Phase274**: `resolve_policy_band(<2M)` の `cap: 3 → 5` + 初期 `max_concurrent_positions: 3 → 5`

## Preflight（必須確認）

対象 config: `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

確認結果（ロード実測）:

- `position_cap=5`（=`max_concurrent_positions=5`）: OK
- `paper_only=true`: OK
- `order_enabled=false`: OK
- `same_symbol_open_policy=no_overlap_replace`: OK

補足:
- 既存の `scripts/run_phase317_tomorrow_paper_trade_preflight.py` は、AM/PM intraday refresh を **CAP<=3 前提で強制チェック**するため、CAP5 では `am_pm_intraday_refresh_will_not_block` が fail します（CAP5 そのものの不整合ではなく、refresh機能のガード条件）。

## 必須回答

1. **Runtime CAP=5 反映完了**: 完了（config YAML の `max_concurrent_positions: 5`）
2. **Phase273 1500k CAP5反映完了**: 完了（`live_start_candidate_1500k.cap=5`）
3. **Phase274 1500k CAP5反映完了**: 完了（`resolve_policy_band(<2M).cap=5`、state初期capも5）
4. **Rollback方法**:
   - `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml` の `max_concurrent_positions: 5 → 3`
   - `src/research/phase273_live_config_forward_shadow_logger.py` の 1500k candidate `cap: 5 → 3`
   - `src/research/phase274_live_config_auto_transition_shadow.py` の 1500k band `cap: 5 → 3`（+ state初期cap）
5. **明日の実行コマンド**:
   - （推奨）intraday refresh を使わずに実行:

```bash
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py ^
  --day-stamp YYYYMMDD ^
  --config kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml
```

   - `scripts/run_phase317_tomorrow_paper_trade_preflight.py` は CAP<=3 制約で fail するため、**当面は利用しない**（または別Phaseで refresh 制約を更新してから利用）。

