# Phase489 — Runtime Observability Audit

**Verdict:** `observability_improvement_candidate`

## 1. 現在通知一覧

See `phase489_current_notifications.csv` (11 event types).

| Channel | Event | Default |
|---------|-------|---------|
| trade_notify | ENTRY | True |
| cap_blocked | CAP BLOCKED | True |
| trade_notify | EXIT | True |
| legacy | HOLD | True |
| legacy | TAKE | True |
| trade_notify | Universe Refresh | True |
| trade_notify | Universe Screening | True |
| trade_notify | Daily Summary | True |
| legacy | HEARTBEAT | True |
| legacy | REJECT | False |
| legacy | ERROR | True |

## 2. 不足項目一覧

See `phase489_observability_gaps.csv` (12 gaps).

## 3. 追加候補一覧

See `phase489_observability_candidates.csv` (10 candidates, C01–C10).

## 4. Discord mockup (Daily Summary enriched)

```
【Daily Summary】 20260619 PM
━━━━━━━━━━━━━━━━━━━━
■ 本日成績 (100株)
trade_count: 48 | win_rate: 56% | PF: 1.24
total_pnl: +25,900円 | stop_rate: 8%
best: 6920 +47,000 | worst: 6920 -60,000

■ Symbol Attribution          ← NEW (C01)
6976: +12,000 (3T, 46% of day) ⚠
4062: -3,500 (2T)
top3_share: 72%

■ Exit Breakdown              ← NEW (C02)
stop_hit: 4 (-18,000)
no_progress: 6 (-22,000)
trailing_mfe: 12 (+41,000)
session_close: 26 (+24,900)
stop_low_mfe: 2 (-15,000)      ← NEW (C05)

■ Runtime Health              ← NEW (C03)
api_errors: 1 | stale_ticks: 3089 | data_gaps: 38
feature_complete: 94.8% | config: …3c45
peak_slots: 5/5 | session: OK

■ Feature Health              ← NEW (C04)
r15_missing: 12% | vwap_dev_missing: 0%
board_change_10m_missing: 31%

■ Reject Funnel (top)         ← NEW (C06)
high_drift: 4385 | stale_price: 31901
late_chase: 12 | max_concurrent: 1658

■ Research Shadow (existing)
LateChase Guard: reject=12
HighDrift Guard: reject=4385
NoProgress Exit: count=6
BoardDynamic Shadow: delta=-1,400
...
```

## 5. Daily Summary mockup

```
【Daily Summary】 20260619 PM
━━━━━━━━━━━━━━━━━━━━
■ 本日成績 (100株)
trade_count: 48 | win_rate: 56% | PF: 1.24
total_pnl: +25,900円 | stop_rate: 8%
best: 6920 +47,000 | worst: 6920 -60,000

■ Symbol Attribution          ← NEW (C01)
6976: +12,000 (3T, 46% of day) ⚠
4062: -3,500 (2T)
top3_share: 72%

■ Exit Breakdown              ← NEW (C02)
stop_hit: 4 (-18,000)
no_progress: 6 (-22,000)
trailing_mfe: 12 (+41,000)
session_close: 26 (+24,900)
stop_low_mfe: 2 (-15,000)      ← NEW (C05)

■ Runtime Health              ← NEW (C03)
api_errors: 1 | stale_ticks: 3089 | data_gaps: 38
feature_complete: 94.8% | config: …3c45
peak_slots: 5/5 | session: OK

■ Feature Health              ← NEW (C04)
r15_missing: 12% | vwap_dev_missing: 0%
board_change_10m_missing: 31%

■ Reject Funnel (top)         ← NEW (C06)
high_drift: 4385 | stale_price: 31901
late_chase: 12 | max_concurrent: 1658

■ Research Shadow (existing)
LateChase Guard: reject=12
HighDrift Guard: reject=4385
NoProgress Exit: count=6
BoardDynamic Shadow: delta=-1,400
...
```

## 6. Shadow Summary mockup

```
Shadow Summary — 20260619 (session aggregate)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Guard rejects (would-block, production gates):
  high_drift_pullback:     4385
  late_chase_guard:          12  ← count only today; no r10/dh detail
  near_day_high_low_mom:    369
  weak_shape:               (in gate, not in Discord)

Exit shadow (board dynamic vs legacy fixed):
  exits: 44 | improved: 2 | delta_yen: -1,400
  stop_hit: 3 | trailing: 10 | session_close: 31

Research forward shadows (Discord today):
  SectorHeat / RiskSizing / EquityDynStop / LiveConfig / Boundary
  → adopt_not_allowed flags only

Missing vs need (Phase487):
  A2_r15_minus_r5 guard would-block: NOT LOGGED
  B2_vwap_extension guard would-block: NOT LOGGED

Recommendation (C07): single Shadow Summary embed mirroring JSON counters
with delta_yen highlights when |delta| > 10k.
```

## 7. Runtime Health mockup

```
Runtime Health — heartbeat / EOD
━━━━━━━━━━━━━━━━━━━━━━━━
Status: 🟢 RUNNING | paper_only | order_enabled=false

Connectivity:
  api_errors: 1        reconnects: 1
  stale_tick_count: 3089  (threshold 120s)
  data_gap_count: 38

Pipeline:
  push_messages: 406,569
  gate_evaluations: 49,681
  quality_fallback_rate: 15.6%

CAP state:
  open_slots: 0/5 | peak_today: 5/5
  same_symbol_overlap_rejects: 934

Config:
  policy: q070_cap3_…_trial
  sha256: 15113c9d…3c45
  structural_exit: trailing_mfe_shadow

Alerts (proposed):
  🔴 if stale_tick_count > 5000/session
  🟡 if feature_complete_rate < 90%
  🔴 if api_errors > 5
```

## 8. Feature Health mockup

```
Feature Health — entry-time coverage
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Session: 20260619 PM | accepted: 48

Bridge stats (from summary JSON today):
  live_feature_complete_rate: 94.82%
  quality_fallback_rate: 15.61%

Per-feature missing on ACCEPTED entries (proposed):
  entry_rise_15min_pct:  8/48 (17%)  ← blocks A2 guard eval
  entry_rise_5min_pct:   2/48 (4%)
  vwap_dev_pct:          0/48 (0%)
  board_change_10m:     15/48 (31%)  ← blocks D2 guard eval
  momentum_continuation: 0/48 (0%)

Staleness on entry:
  data_stale_price rejects: 31901 (funnel)
  data_stale_board rejects: 341

Action: if r15 missing > 20% on accepted → flag Feature Health WARN
```

## 必須回答

1. 不足情報: ['Symbol/day PnL attribution', 'Exit reason + stop_low_mfe breakdown', 'Reject funnel summary', 'Runtime Health block (stale/data/api)', 'Feature Health / missing-feature rates', 'Board tier on ENTRY', 'Guard shadow forward logs (Phase487)', 'Rolling multi-day PnL context']
2. 分析阻害: ['stop_low_mfe count (MFE<0.5% at stop)', 'Dedicated Runtime Health block (stale_tick, data_gap, feature complete rate)', 'Per-feature missing rate (r5/r10/r15, vwap_dev, board_change)']
3. 毎日指標: ['total_pnl_yen_100 + PF + stop_rate', '6976/4062 symbol PnL share', 'stop_low_mfe count', 'exit reason mix (stop / NP / trailing / close)', 'high_drift + late_chase reject counts', 'feature_complete_rate + stale_tick_count', 'board_dynamic shadow delta_yen', 'peak_open_slots / CAP utilization']
4. Discord改善: Add symbol attribution + exit breakdown to Daily Summary embed; enrich ENTRY with board_tier + mom vs cutoff; optional stop_low_mfe tag on EXIT
5. Summary改善: Extend canonical_summary Discord block with reject funnel, runtime/feature health, 5-day rolling PnL
6. Runtime Health改善: Surface api_errors, stale_ticks, data_gaps, config_sha, peak_slots in HEARTBEAT + EOD — alert thresholds
7. Feature Health改善: Log per-feature missing rate on accepted entries; WARN if r15/board_change missing > 20%
8. Runtime変更不要で可能: **True**
9. 優先順位: ['C01_symbol_pnl_daily', 'C02_exit_reason_breakdown', 'C03_runtime_health_block', 'C04_feature_health_block', 'C05_stop_low_mfe_counter', 'C06_reject_funnel']
10. 次アクション: ['Verdict: observability_improvement_candidate', 'Implement C01–C03 in discord_message_builder (Discord/Summary only, no gate change)', 'Add stop_low_mfe counter C05 after C01 validated on 3 sessions', 'Defer C10 guard forward shadow until Phase487 shadow design']

**Verdict:** `observability_improvement_candidate`
