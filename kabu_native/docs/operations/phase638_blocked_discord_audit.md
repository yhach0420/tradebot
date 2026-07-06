# Phase638: Blocked ENTRY Discord Notification Audit

## Problem

Blocked ENTRY candidates (cap / overlap / max_scan / or_cap_full) were not appearing on the trade-cap-blocked Discord channel.

## Root causes

1. **Phase538 or_overlay** remaps `max_concurrent` → `or_cap_full` / `pbv2_cap_full`, but notify checked `max_concurrent` only.
2. **`discord.active` gate** required trade-notify URL even for cap-blocked posts.
3. **Missing routes**: `REJECT_SAME_SYMBOL_OPEN_OVERLAP` and `max_entries_per_scan` never called `notify_entry_cap_blocked`.

Not caused by Phase616 ExtensionBus, Phase629 Stage refactor, or Phase637 operator summary.

## Fix

- `reject_reasons.ENTRY_BLOCKED_DISCORD_NOTIFY_REASONS` — unified notify reasons
- `_notify_entry_blocked_discord` — single path from Stage6 / overlap / scan flush
- `cap_blocked_notify_enabled()` — independent of trade-notify `active`
- Summary: `discord_error_count`, `cap_blocked_notify_*`, System Health lines

## Env

```bash
KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL=...   # required for cap-blocked (no trade-notify fallback)
```

Config: `discord_trade_cap_blocked_webhook_env: KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL`

## Audit

```bash
python scripts/run_phase638_blocked_discord_audit.py --days 7
```

## Tests

```bash
python -m pytest tests/test_phase638_blocked_discord_audit.py tests/test_discord_cap_blocked_notify.py -q
```

## Verdict

`phase638_blocked_discord_audit_done`
