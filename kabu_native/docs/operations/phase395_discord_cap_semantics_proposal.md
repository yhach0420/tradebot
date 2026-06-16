# Phase395 — Discord CAP Semantics Proposal (not implemented)

Generated: 2026-06-15T21:47:46+09:00

**Status:** Proposal only. No `discord_message_builder.py` changes in Phase395.

## Problem

Discord EXIT notifications come from **structural observer** (`observer_exit`), not from Exposure Gate slot release.
On 2026-06-15 PM, **12** EXIT messages at 15:23 were `afternoon_session_close`
while gate slots were **0**. Users may read these as "position CAP exited" when they are observer batch closes.

---

## Proposed ENTRY Notification Additions

```
Gate model: virtual_hold_slot
Position model: observer_structural
CAP note: CAP=3 applies to gate slots, not structural observer open count
```

### Rationale

Runtime ENTRY fires when gate accepts; observer registers a separate structural position that may outlive the 5-minute VH slot.

---

## Proposed EXIT Notification Additions

```
Exit source: structural_observer
Actual gate slot may already be released
Session close burst possible
```

### Rationale

`virtual_hold_expired_ignored_count` shows gate VH expiry does not close observer under `combined_structural_exit_v1`.

---

## Proposed Session Summary Additions

| Field | Example (2026-06-15 PM) |
|-------|-------------------------|
| `gate_max_active_positions` | 3 |
| `observer_open_max_positions` | 16 |
| `session_close_exit_burst_count` | 12 |

---

## Example ENTRY (illustrative)

```
【ENTRY】4062 (信越化学)
ENTRY価格: 22,160
...
Gate model: virtual_hold_slot
Position model: observer_structural
CAP note: CAP=3 = gate slots (~5min), not observer open count
```

## Example EXIT (illustrative)

```
【EXIT】6962
EXIT理由: 午後セッションクローズ
Exit source: structural_observer
Gate slot: already released (VH expired)
Session close burst: 12 exits @ 15:23
```

---

## Example Summary Footer

```
gate_max_active_positions: 3
observer_open_max_positions: 16
session_close_exit_burst_count: 12
```
