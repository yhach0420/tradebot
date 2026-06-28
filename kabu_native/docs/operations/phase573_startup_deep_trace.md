# Phase573 — Runtime Startup Deep Trace

**Verdict:** `phase573_startup_deep_trace_done`
**Period:** 20260529+ | **Reference day:** 20260625

## Phase572 correction

Phase572 labeled 918s as "REST / WebSocket / subscribe / pipeline init". Phase573 proves:

- `live_session_config.generated_at` is stamped at `_live_session_cfg()` **before** `register_symbols_cleared()` and **before** `websockets.connect()`.
- Register + WS + first PUSH complete in **~1s** after `generated_at`.
- The 918s gap is **`make_exposure_gate()` → `build_vol_liq_threshold()` → `prior_vol_liq_scores()` → `load_push_tick_series()`** scanning prior-session push JSONL on disk.

## Reference timeline (20260625 AM)

| Anchor | Time | Source |
|--------|------|--------|
| pilot subprocess start | 08:03:40 | live_session dir stamp |
| safety preflight complete | 08:18:33 | live_session_safety_report.generated_at |
| wait_until_session end | 09:03:00 | policy session_start (after sleep from ~08:18) |
| session_ready (config) | 09:18:18 | live_session_config.generated_at |
| first entry eval | 09:18:19 | entry_scan_audit.jsonl |
| **post-wait gap** | **918s** | 09:03 → 09:18:18 |

## Mandatory answers (20260625)

1. **918s consumption:** `load_push_tick_series()` (~801s self) inside `prior_vol_liq_scores()` loop over 53 prior sessions.
2. **Top20:** dominated by vol_liq push scan; see `phase573_top20_wait.csv`.
3. **WebSocket connect:** ~0.05s (after config write).
4. **Subscribe (register):** ~0.25s REST PUT.
5. **REST token:** ~11.5s (verify_kabu + duplicate issue_token).
6. **REST board:** ~3.2s (inside verify_kabu).
7. **Pipeline build (pre-config):** ~2.6s guards + LiveFeatureBridge.
8. **Event.wait / ws.recv:** ~0.05s post-config; not in 918s gap.
9. **Retry/loops:** REST max_retries=3; prior_vol_liq 53 session iterations.
10. **15 min necessary?** No for WS. Yes for current full push JSONL replay (by design, not API latency).
11. **Improvement:** cache vol_liq threshold between safety and gate; incremental index.
12. **Expected savings:** ~513s/session (median post-wait × 0.85).

PM (20260625): corrected post-wait gap **945s** (safety finished 12:40, after policy 12:33 — wait_until skipped).

## Outputs

- `phase573_function_timeline.csv`
- `phase573_callgraph.csv`
- `phase573_wait_breakdown.csv`
- `phase573_top20_wait.csv`
- `phase573_loop_analysis.csv`
- `phase573_startup_profile.csv`
- `phase573_report.json`

Run: `python kabu_native/scripts/run_phase573_startup_deep_trace.py`

**Note:** Function durations within the measured gap are artifact-anchored allocations guided by static call graph and push-scan metrics (no Runtime instrumentation).
