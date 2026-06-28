# Phase570 — Entry Latency Analysis

**Verdict:** `phase570_entry_latency_analysis_done`
**Period:** 20260529-20260626
**Sessions:** 47 | **Accepted trades:** 3297

## Investigation 1 — Runtime schedule

See `results/reports/phase570_runtime_schedule.csv`.

- AM: 09:03-11:20
- PM: 12:33-15:18
- PUSH: session_start (with --wait-until-session)
- ENTRY eval: allowed_entry_start per session
- 10:00 / 14:30 refresh; lunch break blocks new entries between AM stop and PM start

## Investigation 2 — Entry time distribution

See `phase570_entry_time_distribution.csv`.

- AM first entry median: **09:13** (~10 min after 09:03 allowed start)
- PM first entry median: **12:45** (~12 min after 12:33 allowed start)

## Investigation 3 — Entry latency

Method: session_entry_start_fallback (push_jsonl unavailable). Seconds from allowed_entry_start (or proxy momentum) to entry; push ticks absent in period

- Mean latency: **861.2 sec**
- Median latency: **635.0 sec**

## Investigation 4 — Wait reasons before ENTRY

Primary cause: **board_wait**

Top reasons:
- board_wait: 977
- other_wait: 652
- push_not_received: 638
- momentum_wait: 434
- condition_met: 350

## Investigation 5 — Latency vs PnL

See `phase570_latency_pnl.csv`.

- 0-3min: trades=45 pnl=6000.64 PF=31.0332 win=0.3111
- 3-10min: trades=51 pnl=142999.2 PF=2.2757 win=0.451
- 10-20min: trades=265 pnl=33275.1 PF=1.0802 win=0.4377
- 20min+: trades=2936 pnl=33418.22 PF=1.0072 win=0.4642

- Late (20min+) avg PnL worse than early (0-3min): **True**

## Investigation 6 — 9:30 / 13:00 first ENTRY days

- AM days with first entry >= 09:30: **0** / 18
- PM days with first entry >= 13:00: **2** / 17

PM late examples:
  - 20260617: 2026-06-17T13:10:58+09:00 (condition_met)
  - 20260622: 2026-06-22T13:13:37+09:00 (condition_met)

## Mandatory answers

1. AM/PM schedule: {'am': '09:03-11:20', 'pm': '12:33-15:18'}
2. PUSH start: session_start (with --wait-until-session)
3. ENTRY eval start: allowed_entry_start per session
4. AM first entry median: 09:13
5. PM first entry median: 12:45
6. latency mean sec: 861.2
7. latency median sec: 635.0
8. primary delay cause: board_wait
9. AM 9:30 normal wait: True
10. PM 13:00 normal wait: True
11. late entry PnL worse: True
12. improvement headroom: monitor_only
13. runtime change needed: False
14. next phase: phase571_entry_latency_shadow_monitor
