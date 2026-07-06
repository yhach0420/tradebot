# Phase641: Daily Runner Failure Root-Cause Audit

## Scope

Read-only audit of **32** unique `daily_runner_summary_*.json` files under
`kabu_native/results/` (2026-05-21 .. 2026-07-03), cross-linked with:

- `phase148_am_pm_daily_runner_*.json`
- `results/small_paper/<day>/live_session_*/small_paper_summary.json`
- `errors.jsonl`, `live_session_safety_report.json`
- `small_paper_safety_<day>.json` (preflight failures)

`run_paper_trade.bat` execution logs were **not** retained in the repo.

## Verdict timeline (32 days)

| Verdict | Days |
|---------|------|
| `am_pm_daily_runner_ready` | 23 |
| `intraday_refresh_shadow_ready` | 4 (dry-run refresh validation) |
| `am_failed` | **2** |
| `preflight_blocked` | **3** |
| `pm_failed` | **0** |
| `session_end_failed` / `interrupted` | **0** (never recorded) |

## Failure days

### `am_failed` (2)

| Day | Classification | Impact |
|-----|----------------|--------|
| 2026-06-26 | `pilot_crash_early` | AM pilot exit=1; `live_session_080649` created but **no summary**; artifacts **deleted** |
| 2026-07-01 | `pilot_exit_nonzero` | AM pilot exit=1; **summary exists**, **43 accepted**, stop_reason=`completed` → **false failure** |

### `preflight_blocked` (3)

| Day | Classification | First error |
|-----|----------------|-------------|
| 2026-06-30 | `token_issue` | HTTP 401 ログイン認証エラー (Kabu token) |
| 2026-07-02 | `token_issue` | same |
| 2026-07-03 | `token_issue` | same |

Failed checks: `kabu_register_capacity`, `kabu_station_connection`.

## Mandatory answers

1. **am_failed**: **2 days** (20260626, 20260701)
2. **pm_failed**: **0 days**
3. **preflight_blocked**: **3 days** (20260630, 20260702, 20260703)
4. **最多原因**: `token_issue` (3 preflight) + `pilot_exit_nonzero` / `pilot_crash_early` (2 am_failed)
5. **取引結果ありで失敗扱い**: **Yes — 20260701** (43 accepted, summary complete, verdict=am_failed)
6. **本線停止すべき**: Kabu token/Station 未起動 (401), config safety hard stop, Core10 watchlist missing
7. **warn-onlyでよい**: Discord post failures, websocket reconnect storms, ref_now logging errors (Phase640 fix), core_price_risk warn-only cautions
8. **P0 即対応**:
   - Ops: Kabu Station 起動 + token 確認（3日連続 preflight_blocked）
   - Phase642: verdict policy — summary+completed を success 扱い
   - Phase641b: pilot subprocess stderr キャプチャ
9. **別Phase優先**:
   - P0: `642_daily_runner_verdict_policy`, `641b_runner_subprocess_logging`
   - P1: `643_session_end_exception_isolation`, artifact retention on failure
   - P2: `644_file_lock_retry`, `645_disk_preflight`

## Root-cause detail: 20260701 am_failed

- Preflight: **pass**
- AM session: `live_session_080616`, runtime ~6652s, `stop_reason=completed`
- Pilot subprocess: **exit_code=1** (no exception recorded in phase148)
- Session errors: websocket disconnects + **29× ref_now UnboundLocalError** at 11:20 (Phase640 fixed)
- Daily runner uses `_pilot_failed_hard`: any nonzero exit → `am_failed`, **ignores** summary/trades

## Root-cause detail: 20260626 am_failed

- Preflight: pass
- `new_session_dirs`: `live_session_080649`
- `summary_found=false`, finalize ~09:03 (early vs AM end 11:25)
- Session directory **no longer on disk** — cannot classify first exception from errors.jsonl

## Recommendations (no code changes in Phase641)

See `phase641_recommendations.csv`. Implementation deferred to Phases 642+.

## Audit command

```bash
python scripts/run_phase641_daily_runner_failure_audit.py
```

## Artifacts

- `phase641_verdict_timeline.csv`
- `phase641_failure_classification.csv`
- `phase641_first_exception.csv`
- `phase641_impact_summary.csv`
- `phase641_recommendations.csv`
- `phase641_report.json`

## Verdict

`phase641_daily_runner_failure_audit_done`
