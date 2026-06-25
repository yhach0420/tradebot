# Phase505 — 20260623 Runtime Failure Root Cause Analysis

**Date:** 2026-06-23  
**Primary session:** `results/small_paper/20260623/live_session_122505` (PM)  
**Verdict:** `runtime_bug` (Phase503 timestamp type mismatch — **not** `phase503_regression` on guard logic)

---

## Executive Summary

2026-06-23 PM paper trade ended with **trade_count=0** because:

1. **Runtime bug (primary):** Phase503 `classic_late_chase_rsi_guard._resample_1m_closes()` called `.total_seconds()` on **float** epoch timestamps from the live `symbol_price_ring`. This raised `AttributeError` on every evaluation that passed data-freshness checks, aborting the push iterator and triggering **722 reconnect cycles** (`push_unexpected` every ~10s).

2. **Data / universe filters (contributing, in-window):** All **245** PM entry-window candidates (12:33–15:18) were rejected **before** guard enrichment: `outside_refresh_universe` (157), `data_stale_price` (87), `data_stale_board` (1). **Zero** `am_pm_entry_stop` inside the entry window.

3. **Phase503 guard logic did not block entries:** `classic_late_chase_rsi_over80=0`. The guard never completed enrichment (`rsi14` absent on all events).

**`am_pm_entry_stop=1923` is a red herring** — all 1923 events occurred in the **15:00 hour** (after 15:18 entry_stop), not during the tradable window.

---

## Mandatory Answers

| # | Question | Answer |
|---|----------|--------|
| 1 | Root cause | Phase503 RSI resample expects `datetime`; live pipeline uses `float` seconds (`tick_ts_from_payload`, `symbol_price_ring`). `AttributeError: 'float' object has no attribute 'total_seconds'` → `push_unexpected` → reconnect storm. In-window rejects additionally blocked by stale price / outside refresh universe. |
| 2 | Crash site | `classic_late_chase_rsi_guard.py::_resample_1m_closes` ← `_enrich_trade_for_entry_guards` ← `_process_push_payload` (else branch, post-freshness) → caught in `pilot_runner._loop` as `operation=push_unexpected` |
| 3 | Entry pipeline reach (PM) | push=20276 → feature_complete=2247 → gate_eval=2168 → candidates=2168 → rejected=2168 → **accepted=0**. Enrich/RSI path: **0**. Full ExposureGate (PBv2 guards): **0**. In-window logged rejects: stale+universe only. |
| 4 | Watchdog restart count | **Not recorded** in session artifacts. In-process `reconnect_count=722` (PM), `655` (AM). User-reported watchdog restarts likely secondary (process not detected during reconnect gaps). |
| 5 | Phase503 caused? | **Yes — crash introduced by Phase503 deploy on 6/23** (6/22 PM: `api_errors=13`, 46 accepted; 6/23 PM: `api_errors=722`, 0 accepted). Guard **reject logic** did not fire (`reject=0`). |
| 6 | Rollback required? | **No** — fix timestamp types; keep guard enabled. Emergency workaround: `classic_late_chase_rsi_guard_enabled=false`. |
| 7 | Fix | `classic_late_chase_rsi_guard._resample_1m_closes` uses `(ts - origin) // 60` on float epoch seconds (aligned with `extended_entry_shadow`). Regression test added. |
| 8 | Recurrence after fix | **Low** — live price ring convention documented in guard; unit test covers float ring. |
| 9 | Invalidate 6/23 results? | **Yes** — zero accepted entries, reconnect storm, no valid full-gate path; not comparable to normal runtime. |
| 10 | Safe to start tomorrow? | **Yes, after fix is deployed** (or guard disabled as fallback). |

---

## A. Watchdog / Reconnect

| Metric | AM (`081305`) | PM (`122505`) |
|--------|---------------|---------------|
| `push_unexpected` | 655 | 722 |
| `reconnect_count` | 655 | 722 |
| First error | 09:27:43 | 13:14:18 |
| Median interval | ~10s | ~10s |
| Exception | `'float' object has no attribute 'total_seconds'` | same |
| `stop_reason` | `session_end` | `session_end` |

Reconnect is **in-process** (`pilot_runner._reconnect_push`), not session restart. No traceback persisted in `errors.jsonl` (only `str(exc)`).

---

## B. push_unexpected Analysis

| Field | Value |
|-------|-------|
| Log site | `pilot_runner.py` `_loop` → `_log_api_error("push_unexpected", e)` |
| Trigger | `_enrich_trade_for_entry_guards` → `compute_classic_late_chase_rsi_guard_fields` → `compute_rsi14_at_entry` → `_resample_1m_closes` |
| Expression | `(ts - origin).total_seconds()` |
| Expected type | `datetime` (Phase503 research code) |
| Actual type | `float` (epoch seconds from `tick_ts_from_payload` / `append_price_tick`) |

---

## C. Entry Pipeline (PM)

See `results/reports/phase505_runtime_pipeline_breakdown.csv`.

**Drop point:** Candidates that would pass freshness crash before logging; logged candidates are exclusively early rejects (stale / universe / post-close am_pm).

---

## D. Phase503 Impact

| File | Role on 6/23 |
|------|----------------|
| `classic_late_chase_rsi_guard.py` | **Crash** — datetime assumption |
| `exposure_gate.py` | Wired; never reached with RSI fields |
| `pilot_runner.py` | Passes float `entry_ts` + float price ring to guard |

`classic_late_chase_rsi_over80=0` confirms guard **logic** did not reject.

---

## E. Reject Funnel — `am_pm_entry_stop=1923`

- **In entry window (12:33–15:18):** 0  
- **Hour 15:00 only:** 1923  
- **Explanation:** After `entry_stop` (15:18), every push tick still evaluates `am_pm_entry_stop`. Inflates summary counter; **not** the cause of zero trades.

---

## F. Runtime Health (PM)

| Metric | Value | Notes |
|--------|-------|-------|
| `api_errors` | 722 | All `push_unexpected` |
| `reconnect_count` | 722 | 1:1 with api_errors |
| `stale_tick_count` | 46 | Push age > threshold |
| `data_gap_count` | 229 | Inter-tick gap > threshold |
| `intraday_refresh` | 2 | Refresh notifications OK |

Reconnect storm likely **amplified** stale/gap counts vs normal sessions.

---

## Fix Diff (summary)

```diff
# classic_late_chase_rsi_guard.py
- minute_key = int((ts - origin).total_seconds() // 60)
+ minute_key = int((ts - origin) // 60)  # float epoch seconds (live price ring)
```

Types: `Sequence[tuple[float, float]]`, `entry_ts: float`.

---

## Artifacts

- `results/reports/phase505_runtime_failure_root_cause.csv`
- `results/reports/phase505_runtime_pipeline_breakdown.csv`
- `results/reports/phase505_runtime_errors.csv`
- `results/reports/phase505_summary.json`

## Run

```bash
python kabu_native/scripts/run_phase505_runtime_failure_root_cause.py
```

---

## Classification

**`runtime_bug`** — Phase503 deployment introduced a live-path type mismatch. Contributing **`data_provider_issue`** (stale ticks / universe) for in-window logged rejects, but insufficient alone to explain 6/22→6/23 api_error spike.
