# Discord Notification Data Spec

## Envelope fields

notification_id, category, severity, event_type, title, trading_date, session_id,
am_pm, symbol, occurred_at_jst, source_module, dedupe_key, correlation_id,
actual_or_shadow, action_required, operator_action, artifact_path,
payload_schema_version, payload_hash

## Audit files

- notification_events.jsonl
- notification_failures.jsonl
- notification_dead_letter.jsonl
- notification_summary.json

## Forbidden in storage

Webhook URL, API token/password, Authorization, HoldID, account number.

## Dedupe store

`runtime/discord_notification_dedupe.jsonl`
