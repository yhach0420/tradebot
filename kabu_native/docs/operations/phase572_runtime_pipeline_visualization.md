# Phase572 — Runtime Pipeline Visualization

**Verdict:** `phase572_runtime_pipeline_visualization_done`
**Period:** 20260529-20260627 | **Days:** 22
**Reference day:** 20260625

## Key finding

The 09:03→09:18 and 12:33→12:56 gaps are **post-`wait_until_session` initialization**
(REST token, WebSocket connect, symbol subscribe, pipeline setup), NOT Universe generation or sleep.
Sleep happens **before** policy start when pilot subprocess launches early (~08:03 AM / ~12:25 PM).

## Mandatory answers

1. After wait_until_session(09:03): REST token + WS connect + subscribe + pipeline init (~918s on 20260625). NOT sleep. Pilot subprocess started ~08:03, slept until 09:03.
2. After wait_until_session(12:33): same init chain (~1384s on 20260625). Pilot subprocess started ~12:25 after PM universe regen.
3. Partial — sleep occurs BEFORE policy start (pilot early launch + wait_until_session). Gap AFTER policy is init/IO.
4. Universe CSV built at daily_runner start (~07:48 on 20260625), hours before pilot. Not the 09:03-09:18 gap.
5. Yes — sec_policy_to_session_ready averages 833s (WS+register+setup after wait_until returns).
6. After session_ready, first_push typically 0-2s. Long push_wait in Phase571 is stale ticks post-start.
7. Partial — wait_until_session + early pilot launch is by design; 15-23min post-policy init is not documented.
8. Potential init optimization ~833s/session (WS/subscribe batching); sleep is intentional.
9. Optional: defer pilot subprocess until closer to session_start; parallelize token+register; startup timing logs.
10. 416.0
11. False
12. phase573_entry_pipeline_startup_shadow_monitor

