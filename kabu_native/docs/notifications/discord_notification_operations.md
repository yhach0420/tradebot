# Discord Notification Operations

## User command (unchanged)

```bat
cd C:\Users\yhach\Documents\tradebotfile
.\run_paper_trade_checked.bat
```

## Readiness

```bat
python -m small_paper.check_discord_notification_readiness
```

Default: **no external Discord send**.

Explicit test (one message only):

```bat
python -m small_paper.check_discord_notification_readiness --send-test
```

## Env keys

- `KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL` — actual ENTRY/EXIT/Summary
- `KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL` — cap blocked
- `KABU_DISCORD_OPERATIONS_WEBHOOK_URL` — Paper blocked / runner ops
- `KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL` or `KABU_MARKET_CAPTURE_WEBHOOK_URL`
- `KABU_DISCORD_RESEARCH_WEBHOOK_URL` or `KABU_SHADOW_DISCORD_WEBHOOK_URL`
- `KABU_DISCORD_CRITICAL_WEBHOOK_URL`

Missing webhook → `SKIPPED_WEBHOOK_NOT_CONFIGURED` + local audit. Paper/Capture continue.

## Audit

`kabu_native/results/notifications/YYYYMMDD/`
