# Phase422 — Intraday Refresh Safety Guard CAP5 Update

Generated: 2026-06-17

## 目的

Phase421 で Runtime / 150万円 band を **CAP5** に更新したが、Phase317 preflight が
`refresh_requires_max_concurrent_lte_3` により FAIL していた。
intraday refresh の safety guard を **CAP<=5** + Phase421 正式設定に合わせて更新。

## 変更内容

### `src/universe/intraday_refresh.py`

- `MAX_CONCURRENT_FOR_INTRADAY_REFRESH = 5` を追加
- `check_intraday_refresh_policy()` を拡張:
  - 旧: `max_concurrent_positions <= 3` 必須
  - 新: `max_concurrent_positions <= 5` 必須（CAP6+ は FAIL）
  - 追加ガード（refresh enabled 時）:
    - `position_cap_mode = true`
    - `same_symbol_open_policy = no_overlap_replace`
    - `paper_only = true`
    - `order_enabled = false`

### `src/runner/am_pm_daily_runner.py`

- `_intraday_refresh_preflight()` から stale な `intraday_refresh_requires_cap3` チェックを削除
- 新ガード引数を policy 呼び出しに渡すよう更新

### `scripts/run_phase317_tomorrow_paper_trade_preflight.py`

- policy 呼び出しに runtime guard 引数を追加
- check details に `max_concurrent_positions` / `position_cap_mode` / `same_symbol_open_policy` / `order_enabled` / `paper_only` を出力

## 検証結果

```bash
python kabu_native/scripts/run_phase317_tomorrow_paper_trade_preflight.py \
  --day-stamp 20260617 \
  --config kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml \
  --skip-kabu --skip-discord-ping \
  --output results/reports/phase317_tomorrow_paper_trade_preflight_20260617.json
```

- `preflight_ok`: **true**
- `failed_checks`: **[]**
- `am_pm_intraday_refresh_will_not_block`: **true**
- details:
  - `max_concurrent_positions`: 5
  - `position_cap_mode`: true
  - `same_symbol_open_policy`: no_overlap_replace
  - `order_enabled`: false
  - `paper_only`: true

## テスト

`tests/test_phase422_intraday_refresh_cap5_guard.py` — **6 passed**

## Rollback

CAP5 採用を維持するため **CAP3 へのロールバックは不要**。
万一 guard のみ戻す場合は `MAX_CONCURRENT_FOR_INTRADAY_REFRESH` と policy 条件を revert。

## 禁止事項遵守

- Runtime Entry/Exit: 未変更
- Universe 選定ロジック: 未変更（policy チェックのみ）
- Order / Discord: 未変更
