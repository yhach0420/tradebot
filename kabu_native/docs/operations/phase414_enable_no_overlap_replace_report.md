# Phase414 — Enable no_overlap_replace in Production YAML

Generated: 2026-06-16

## Conclusion

Phase413 backfill で採用条件を満たしたため、本番 paper 用 YAML に `same_symbol_open_policy: no_overlap_replace` を有効化した。

これは **Entry条件変更ではなく**、同一銘柄保有中の重複 ENTRY 制御（overlap_replaced_review 連鎖の抑制）の修正である。

## Phase413 結果（採用根拠）

| 指標 | baseline | no_overlap_replace |
|------|----------|-------------------|
| trade_count | 1529 | 681 |
| overlap_replaced_review | 999 | 151 |
| total_pnl_yen_100 | 130,767.6 | 130,767.6 |
| PF | 1.101 | 1.1234 |
| maxDD | 105,301.93 | 102,282.41 |
| median_hold_sec | 55 | 313 |

verdict: **runtime_adoption_candidate**

## YAML 変更内容

**ファイル:** `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

```yaml
same_symbol_open_policy: no_overlap_replace
```

- 未指定時のデフォルト: `replace`（既存互換）
- `order_enabled: false` / `paper_only: true` は変更なし

## Runtime 反映内容（Phase413 で実装済み）

同一 symbol が observer で open 中に同一 symbol の ENTRY が来た場合:

- 既存 position は閉じない
- `close_for_overlap()` を呼ばない
- 新 ENTRY は reject
- `reject_reason = REJECT_SAME_SYMBOL_OPEN_OVERLAP`
- 既存 position は structural EXIT まで維持
- EXIT 後の同一 symbol 再 ENTRY は許可

**summary 追加項目:**

- `same_symbol_open_policy`
- `rejected_by_same_symbol_open_overlap`
- `same_symbol_overlap_reject_count`
- `overlap_replaced_review_count`

## Preflight（明日の paper 前）

daily runner が本 YAML を読むことを確認:

```bash
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py \
  --universe-mode core10-dynamic40-price-risk-filter-shadow \
  --enable-intraday-refresh \
  --exit-policy-shadow trailing-mfe
```

確認コマンド（設定のみ）:

```bash
python -c "
from pathlib import Path
from small_paper.config import load_pilot_config
p = Path('configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml')
cfg = load_pilot_config(p)
print('same_symbol_open_policy=', cfg.same_symbol_open_policy)
print('order_enabled=', cfg.order_enabled, 'paper_only=', cfg.paper_only)
"
```

期待: `same_symbol_open_policy= no_overlap_replace`

## 明日見る指標

| 指標 | 期待変化 |
|------|----------|
| trade_count | 大幅減少 |
| overlap_replaced_review_count | 激減 |
| REJECT_SAME_SYMBOL_OPEN_OVERLAP | 増加 |
| median hold | 上昇 |
| Phase409 boundary eligible rate | 上昇傾向 |
| position_cap max_open | <= 3 維持 |

## Rollback

1. YAML で `same_symbol_open_policy: replace` に戻す、または
2. `same_symbol_open_policy` 行を削除（デフォルト `replace`）

## 変更禁止の遵守

- Universe / Entry score / Exit / Order / Discord 本線: **変更なし**
- Phase409 Boundary shadow: **継続**（本変更とは独立）
