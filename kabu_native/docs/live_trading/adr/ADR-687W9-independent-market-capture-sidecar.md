# ADR-687W9 — Independent Market Capture Sidecar

## Status

Accepted (Phase687W9)

## Context

Paper Runtime failures (preflight, crash, seal) must not erase the day's Kabu PUSH tape.
Capture must not trade or couple into strategy paths.

## Decision

1. **Process separation** — Sidecar is a separate PID/process group from Paper and the checked runner parent.
2. **Shared resource** — Only Kabu symbol registration is shared; hard limit 50; file lock + manifest.
3. **Ownership** — Checked runner coordinates registration from Paper universe SoT; Sidecar is follower; Sidecar never `unregister/all`.
4. **Topology** — Prefer passive dual WebSocket; do not assume official multi-WS guarantee; probe and classify.
5. **Fallback** — Scaffold `SINGLE_INGRESS_LOCAL_FANOUT`; Paper default PushSource stays `KABU_DIRECT`; no automatic Monday switch.
6. **Gap semantics** — Account all drops/overflows; lunch/after-close idle is not an error.
7. **Interference control** — BELOW_NORMAL priority, buffered writer, separate disk tree; Forward interference audit later.
8. **Capture Seal** — Independent of Paper Session Seal; produced at 15:35 finalize even if Paper seal fails.
9. **Orders** — Complete separation: live/order false, submit/cancel 0, no production enablement.

## Consequences

- Checked runner start order changes; Capture starts before Paper preflight.
- Paper preflight failure displays `[PAPER BLOCKED - CAPTURE CONTINUES]`.
- Capture start failure blocks Paper by default (`CAPTURE_REQUIRED_NOT_READY`).

## Verdict meaning

`INDEPENDENT_MARKET_CAPTURE_READY` = market data capture foundation ready.
It does **not** authorize or implement production orders.
