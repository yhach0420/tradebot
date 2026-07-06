# Phase639: Discord Live Notification Smoke Test

## Purpose

Verify Phase637/638 Discord changes with **real webhook HTTP POST** (not dry-run).

## Env required

```bash
KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL=...
KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL=...      # trade-notify
KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL=...   # cap-blocked only (no fallback)
```

## Run

```bash
python scripts/run_phase639_discord_live_smoke.py
```

Sends 8 live messages + 1 intentional missing-webhook probe (error count).

## Tests

| test_id | Channel | Content |
|---------|---------|---------|
| trade_notify_heartbeat | trade_notify | HEARTBEAT |
| operator_daily_summary | trade_notify | PM Summary + operator sections |
| cap_blocked_max_concurrent | cap_blocked | CAP BLOCKED |
| cap_blocked_overlap | cap_blocked | overlap reason |
| cap_blocked_max_scan | cap_blocked | max_entries_per_scan |
| rise5_shadow_summary | trade_notify | Rise5 Shadow block |
| gate_dominance_critical | trade_notify | CRITICAL alert |
| discord_health_summary | trade_notify | System Health + discord_errors |

## Artifacts

`results/reports/phase639_discord_live_smoke/`

## Verdict

`phase639_discord_live_smoke_done`
