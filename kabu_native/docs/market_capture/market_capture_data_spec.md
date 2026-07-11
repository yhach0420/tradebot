# Market Capture Data Spec

## Root

`kabu_native/data/market_capture/YYYYMMDD/`

## Files

| File | Description |
|------|-------------|
| `capture_manifest.json` | Session metadata, provenance, symbols, topology |
| `capture_status.json` | Live status |
| `capture_heartbeat.json` | Freshness |
| `registration_manifest.json` | Copy of coordinated registration |
| `push_part_NNNN.jsonl` | Append-only PUSH events |
| `capture_gaps.jsonl` | Accounted gaps (overflow, write fail) |
| `disconnect_events.jsonl` | Disconnect/reconnect |
| `registration_generation_events.jsonl` | Intraday generation changes |
| `capture_summary.json` | Completeness metrics |
| `capture_seal.json` | Finalize hashes (≠ Paper seal) |
| `restart_history.jsonl` | Restart audit |
| `capture.pid` | Single-instance PID |
| `operator_stop.flag` | Operator stop |

## Event record

- `schema_version`, `capture_session_id`, `sequence`
- `received_at_jst`, `received_at_utc`, `received_monotonic_ns`
- Extracted board fields (search aids)
- `original_payload` — full received payload (secrets redacted)

## Forbidden in storage

API password, token, Authorization, account number, HoldID, order info.

## Provenance

Live: `LIVE_KABU_PUSH_CAPTURE`, `fixture=false`, `synthetic=false`, `test_mode=false`.

Synthetic/test must never count as live Forward / W4S sessions.

## Gap semantics

- Lunch / after-close with no PUSH is **not** an error (market-event gap vs heartbeat gap).
- Queue overflow / write failure **must** be recorded; silent drop forbidden.

## Status values

`CAPTURE_COMPLETE`, `CAPTURE_NO_MARKET_EVENTS`, `CAPTURE_PARTIAL`,
`CAPTURE_DEGRADED`, `CAPTURE_REGISTRATION_MISMATCH`, `CAPTURE_DISCONNECTED`,
`CAPTURE_WRITE_FAILED`.
