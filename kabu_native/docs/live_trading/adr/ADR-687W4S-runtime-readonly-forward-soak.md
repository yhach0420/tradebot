# ADR-687W4S — Runtime Read-Only Forward Soak

- **Status:** READONLY_SOAK_IN_PROGRESS
- **Date:** 2026-08-17

## Context

W4 implemented Runtime dry-run wiring + Kabu read-only. Forward soak confirms live Paper sessions.

## Decision

1. Collect ≥3 Paper sessions with `soak_session_snapshot.json`.
2. Require ≥1 successful live read-only acquisition.
3. mapping loss / duplicate intent / reservation leak / submit/cancel must be 0.
4. Do not auto-map client/token missing to weekend unavailable.
5. Latency SLA: accept_to_would_submit p95 < 100ms; journal commit p95 < 50ms.
6. Never enable production orders during soak.

## Forward measured (latest evaluator run)

- sessions: 1
- readonly success sessions: 1
- probe now: `TOKEN_REQUEST_FAILED`
- verdict: `READONLY_SOAK_IN_PROGRESS`

## Rollback

`live_order_safety_sm_enabled: false`. Flags remain false.
