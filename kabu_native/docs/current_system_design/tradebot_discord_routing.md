# Discord W10 Routing

SoT: `src/notify/discord_notification_router.py` + `discord_notification_model.py`

## Category → Env

| category | env_keys | rate_limit | fallback |
| --- | --- | --- | --- |
| TRADE_ACTUAL | KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL / KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL | dedupe only | none |
| SESSION_SUMMARY | KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL / KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL | dedupe only | none |
| CAP_BLOCKED | KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL | 1 per symbol/session/reason via dedupe | none |
| OPERATIONS | KABU_DISCORD_OPERATIONS_WEBHOOK_URL | 15 min | none |
| MARKET_CAPTURE | KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL / KABU_MARKET_CAPTURE_WEBHOOK_URL | 15 min | none |
| RESEARCH_SHADOW | KABU_DISCORD_RESEARCH_WEBHOOK_URL / KABU_SHADOW_DISCORD_WEBHOOK_URL | AM/PM by caller | none; no cross to TRADE |
| CRITICAL_SAFETY | KABU_DISCORD_CRITICAL_WEBHOOK_URL | 30 min | CRITICAL_OPERATIONS_FALLBACK_DEFAULT=false |

## Behaviors
- Async worker (`discord_notification_worker.py`)
- Fail-open on send errors
- Dedupe (`discord_notification_dedupe.py`)
- Retry + HTTP 429 handling
- Rate limit (`discord_notification_rate_limit.py`)
- Audit + dead-letter
- Secret masking
- Demo sender separated (`discord_demo_sender.py`)
- Actual vs Shadow separation; **no cross-category webhook fallback** (CRITICAL→OPS default false)
- Unconfigured webhook → SKIP
