# Phase430 — Intraday Refresh Disabled Audit (20260617)

Generated: 2026-06-17  
Verdict: **operational_argv_omission** (preflight false-positive)

調査のみ。Runtime / YAML / Entry / Exit / Order / Discord は未変更。

---

## 結論（必須回答）

| 質問 | 回答 |
|------|------|
| refresh が実行されなかった直接原因 | 実際の daily runner 起動時に **`--enable-intraday-refresh` が付いていなかった**（`enable_intraday_refresh=false` が summary / pilot に伝播） |
| バグか運用ミスか | **運用ミス（argv 省略）** が主因。加えて **Phase317 preflight が actual argv を検証せず false PASS** した設計ギャップあり |
| 修正すべき箇所 | (1) 運用コマンドにフラグ必須 (2) Phase317 preflight を actual コマンドと一致させる (3) Phase421 レポートの「refresh なし推奨」を obsolete 化 |
| 明日の正しいコマンド | 下記「正式コマンド」参照 |
| preflight と actual run を一致させる方法 | preflight 後に `daily_runner_commands_YYYYMMDD.json` の `phase148_script` / `am_argv` を目視確認；preflight に「本番 argv に `--enable-intraday-refresh` 必須」チェックを追加 |

---

## 1. 20260617 に実行された daily runner コマンド

**記録元:** `results/reports/phase148_am_pm_daily_runner_20260617.json`（`options`）  
`results/reports/daily_runner_commands_20260617.json`（`phase148_script`）

```text
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py \
  --day-stamp 20260617 \
  --universe-mode core10-dynamic40-price-risk-filter-shadow \
  --config kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml
```

- `--enable-intraday-refresh`: **なし**
- `--exit-policy-shadow trailing-mfe`: **なし**（config が trailing_mfe_shadow YAML のため exit 自体は trailing-mfe 相当）
- `options.enable_intraday_refresh`: **false**

**前日 20260616 との差分:** 20260616 は `enable_intraday_refresh: true`、AM/PM pilot argv に `--enable-intraday-refresh` + `--intraday-refresh-csv` あり。

---

## 2. `--enable-intraday-refresh` の有無

| レイヤ | 20260617 |
|--------|----------|
| daily runner CLI | **なし** |
| AM pilot argv | **なし** |
| PM pilot argv | **なし** |
| small_paper summary | `intraday_refresh_enabled: false` |

---

## 3. daily runner の default

`run_core10_dynamic40_am_pm_daily_runner.py`:

```python
parser.add_argument("--enable-intraday-refresh", action="store_true", ...)
```

`DailyRunnerOptions.enable_intraday_refresh: bool = False`

→ **default は refresh=false（opt-in）**。フラグを付けない限り refresh は無効。

---

## 4. preflight vs actual argv の不一致

**Phase317 preflight** (`phase317_tomorrow_paper_trade_preflight_20260617.json`):

- `preflight_ok`: **true**
- `am_argv_has_intraday_refresh`: **true**
- `pm_argv_has_intraday_refresh`: **true**

**しかし actual run:**

- `am_argv_has_intraday_refresh`: **false**
- `pm_argv_has_intraday_refresh`: **false**

**理由:** `run_phase317_tomorrow_paper_trade_preflight.py` の `_check_am_pm_refresh()` は検証用に

```python
DailyRunnerOptions(..., enable_intraday_refresh=True)  # 常に True
```

で `pilot_command_argv()` を組み立てる。**オペレーターが朝に打つ実コマンドは見ていない。**

→ Phase422 で policy guard は PASS したが、「朝の実行コマンドにフラグがあるか」は検証していない。

---

## 5. `intraday_refresh_enabled=false` の生成元

| ファイル | ロジック |
|----------|----------|
| `runner/am_pm_daily_runner.py` | `build_daily_runner_summary_payload`: `"intraday_refresh_enabled": bool(state.options.enable_intraday_refresh)` |
| `small_paper/pilot_runner.py` | `run_pilot(..., enable_intraday_refresh=...)` → state → summary |

20260617 は daily runner options が false のため、summary / live_session_config とも false。

---

## 6. 10:00 / 14:30 refresh 関連アーティファクト

| アーティファクト | 20260617 |
|------------------|----------|
| `universe_*_am_refresh1000_20260617.csv` | **不存在** |
| `universe_*_pm_refresh1430_20260617.csv` | **不存在** |
| `phase157_intraday_refresh_runner_review.json` | **未生成** |
| pilot log の `intraday_refresh` イベント | **0** |
| AM universe | `universe_core10_dynamic40_price_risk_am_20260617.csv`（開場前のみ） |
| PM universe | `universe_core10_dynamic40_price_risk_pm_20260617.csv`（昼休み再生成のみ） |

refresh CSV は `enable_intraday_refresh=true` の AM prep 時のみ build される。20260617 はスキップされたため 10:00 / 14:30 refresh は実行不可能だった。

---

## 7. Phase422 preflight が検証していたこと

Phase422 は **intraday refresh policy guard の CAP5 対応** のみ:

- `max_concurrent_positions <= 5`
- `position_cap_mode = true`
- `same_symbol_open_policy = no_overlap_replace`
- `paper_only = true`, `order_enabled = false`

preflight 内で `enable_intraday_refresh=True` を仮定した場合に policy / argv が通ることを確認。**実際の朝のコマンドラインは対象外。**

---

## 8. `--enable-intraday-refresh` を必須化すべきか

**Yes（運用上必須）。** 理由:

- Stack C 正式設計は 10:00 / 14:30 intraday refresh 付き daily runner
- default=false のため省略すると静かに無効化される（今回の事象）
- 20260616 は有効、20260617 は無効 — 日によって挙動が変わる

コード default 変更は本 Phase スコープ外（調査のみ）。当面は **argv 必須 + preflight 整合** で足りる。

---

## 想定原因の検証

> actual 実行コマンドに `--enable-intraday-refresh` が無かった

**confirmed.** `phase148` options / `daily_runner_commands` が一致して示す。

**補足要因:** `docs/operations/phase421_cap5_runtime_enable_report.md` が CAP5 移行直後に refresh **なし** コマンドを「明日の実行」として記載。Phase422 で guard は直ったが、運用コマンドが更新されず 20260617 に持ち越された可能性が高い。

---

## 明日の正式コマンド（20260618 想定）

**Windows (PowerShell / cmd):**

```bat
python kabu_native\scripts\run_core10_dynamic40_am_pm_daily_runner.py ^
  --day-stamp 20260618 ^
  --universe-mode core10-dynamic40-price-risk-filter-shadow ^
  --config kabu_native\configs\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml ^
  --enable-intraday-refresh ^
  --exit-policy-shadow trailing-mfe
```

**Unix:**

```bash
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py \
  --day-stamp 20260618 \
  --universe-mode core10-dynamic40-price-risk-filter-shadow \
  --config kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml \
  --enable-intraday-refresh \
  --exit-policy-shadow trailing-mfe
```

**朝の確認チェックリスト:**

1. `run_phase317_tomorrow_paper_trade_preflight.py --day-stamp YYYYMMDD` → `preflight_ok=true`
2. daily runner 起動後 `results/reports/daily_runner_commands_YYYYMMDD.json` で:
   - `phase148_script` に `--enable-intraday-refresh`
   - `am_argv` / `pm_argv` に `--enable-intraday-refresh` と `--intraday-refresh-csv`
3. 引け後 summary で `intraday_refresh_enabled: true`

---

## preflight と actual run を一致させる方法（推奨）

1. **短期（運用）:** preflight PASS 後も上記 checklist で `daily_runner_commands` を確認
2. **中期（preflight 改善案・別 Phase）:**
   - Phase317 に `--production-argv-file` または `--require-intraday-refresh` を追加
   - hardcode `enable_intraday_refresh=True` ではなく、ドキュメント化された canonical argv 文字列と照合
   - mismatch 時は `preflight_ok=false` + `actual_argv_missing_intraday_refresh`

---

## 成果物

- `results/reports/phase430_intraday_refresh_disabled_audit.json`
- `docs/operations/phase430_intraday_refresh_disabled_audit_report.md`
