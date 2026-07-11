# Discord Notification Design (Phase687W10)

## Purpose

Separate TradeBot Discord traffic by category so actual trades, Paper ops,
Market Capture, research shadows, and critical safety never mix or flood channels.

Discord failures must **fail-open**: Paper Runtime, Capture, ENTRY/EXIT, SafetySM continue.

## Categories

| Category | Ownership | Webhook keys |
|----------|-----------|--------------|
| TRADE_ACTUAL | Paper Runtime | `KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL` (+ legacy compat) |
| SESSION_SUMMARY | Paper Runtime | same trade-notify |
| CAP_BLOCKED | Paper Runtime | `KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL` |
| OPERATIONS | Checked Runner | `KABU_DISCORD_OPERATIONS_WEBHOOK_URL` (no auto-fallback) |
| MARKET_CAPTURE | Capture Sidecar | `KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL` / `KABU_MARKET_CAPTURE_WEBHOOK_URL` |
| RESEARCH_SHADOW | Research | `KABU_DISCORD_RESEARCH_WEBHOOK_URL` / `KABU_SHADOW_DISCORD_WEBHOOK_URL` |
| CRITICAL_SAFETY | Safety | `KABU_DISCORD_CRITICAL_WEBHOOK_URL` (ops fallback default **false**) |

External channels / webhooks are **never** auto-created.

## Async worker

HTTP send runs on a bounded priority queue. PUSH / ENTRY / EXIT / Capture writer
are never blocked on Discord I/O.

## Dedupe / rate limit

Persistent `runtime/discord_notification_dedupe.jsonl`.
OPERATIONS / CAPTURE: 15m same-state suppress. CRITICAL: 30m continuation; severity upgrade re-notifies.

## Secrets

Webhook URLs, tokens, passwords, HoldIDs never written to logs or phase reports.
