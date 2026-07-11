# Market Capture Design (Phase687W9)

## Purpose

Independent process captures Kabu PUSH market data so Paper preflight failure,
runtime crash, or Seal failure does not erase the day's market tape.

Recorder does **not** trade, select universe, or call Paper ENTRY/EXIT.

## Process isolation

- Sidecar PID ≠ Paper PID
- Windows: new process group / detached
- Survives Paper exit, parent checked-runner exit
- Output root: `kabu_native/data/market_capture/YYYYMMDD/`
- Never writes Paper `results/small_paper`, journals, soak, canonical, positions

## Shared resource: registration only

Kabu register limit = **50**.

- Checked runner resolves universe SoT and coordinates registration under
  `runtime/market_registration.lock` + `runtime/market_registration_manifest.json`
- Sidecar is a **follower** (no `unregister/all`)
- Intraday refresh: Paper universe SoT → lock → diff → update → generation event

## Topology

Preferred: `PASSIVE_DUAL_WEBSOCKET` (Paper keeps existing WS; Sidecar opens another).

Official multi-WS is **not** assumed. Probe classifies:

- `DUAL_WS_COMPATIBLE`
- `DUAL_WS_CONNECTION_ONLY_UNVERIFIED` (weekend)
- `DUAL_WS_INCOMPATIBLE` / `DUAL_WS_RECONNECT_STORM` / `DUAL_WS_EVENT_DIVERGENCE`

Fallback scaffold: `SINGLE_INGRESS_LOCAL_FANOUT` (capture first, localhost fanout).
Paper PushSource default remains `KABU_DIRECT`. No Monday auto-switch.

## Writer

Buffered append-only JSONL, dedicated thread, rotate 256MB/30m, flush 250ms/100,
fsync 5s. Queue overflow → emergency append or gap + DEGRADED (never raise into Paper).

## Capture Seal

At 15:35 JST finalize: hash/size/row counts for parts, gaps, disconnects,
registration + capture manifests/summary → `capture_seal.json`.
Independent of Paper Session Seal.

## Safety

- `live_trading_enabled=false`, `order_enabled=false`
- actual submit/cancel = 0
- secrets redacted; never store password/token/Authorization/account/HoldID/orders
- READY means capture readiness only — **not** production order authorization
