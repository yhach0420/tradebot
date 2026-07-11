# ADR-687W10 — Discord Notification Reliability and UX

## Status

Accepted (Phase687W10)

## Decision

1. Classify all Discord traffic into seven categories with explicit ownership.
2. Route via env keys; never auto-create channels/webhooks; missing webhook → skip + audit.
3. Async worker with bounded queue; Discord I/O never blocks Paper/Capture/ENTRY/EXIT.
4. Persistent dedupe + category rate limits; CRITICAL severity upgrade may re-notify.
5. Actual and Shadow never share the same notification body or PnL total.
6. Existing `notify_entry` / `notify_exit` / `notify_summary` / `notify_entry_cap_blocked` remain as adapters (no dual send).
7. Fail-open on Discord errors.

## Consequences

- New package under `src/notify/discord_notification_*.py`
- Checked Runner owns PAPER BLOCKED; Capture owns capture lifecycle; Runtime owns trades
- READY means notification foundation ready — not webhook provisioning or order authorization
