# TradeBot Environment Variables

Version 2026.07.12. Webhook **values** are never documented.

## Discord (W10)

| category | env_keys | rate_limit | fallback |
| --- | --- | --- | --- |
| TRADE_ACTUAL | KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL / KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL | dedupe only | none |
| SESSION_SUMMARY | KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL / KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL | dedupe only | none |
| CAP_BLOCKED | KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL | 1 per symbol/session/reason via dedupe | none |
| OPERATIONS | KABU_DISCORD_OPERATIONS_WEBHOOK_URL | 15 min | none |
| MARKET_CAPTURE | KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL / KABU_MARKET_CAPTURE_WEBHOOK_URL | 15 min | none |
| RESEARCH_SHADOW | KABU_DISCORD_RESEARCH_WEBHOOK_URL / KABU_SHADOW_DISCORD_WEBHOOK_URL | AM/PM by caller | none; no cross to TRADE |
| CRITICAL_SAFETY | KABU_DISCORD_CRITICAL_WEBHOOK_URL | 30 min | CRITICAL_OPERATIONS_FALLBACK_DEFAULT=false |

## Other

| Variable | Role |
|---|---|
| PYTHONPATH | `src;<repo>` set by PS1 / BAT |
| PYTHONIOENCODING | utf-8 |
| Kabu API credentials | via Kabu Station / local env (not in docs) |

## Config file

- `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`
- SHA256: `a20e40ed1bf52624478ecfecf73270a2e3f8df293b37ebcf9d5534ba410e4690`
- Loaded by: `small_paper.config.load_pilot_config`
