"""
Phase396: Runtime Position-CAP mode helpers.

When position_cap_mode=true, CAP is enforced on observer open positions until
structural EXIT — not on Exposure Gate virtual-hold slots.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.exposure_gate import REJECT_MAX_CONCURRENT

JST = ZoneInfo("Asia/Tokyo")
POSITION_CAP_RELEASE_STRUCTURAL_EXIT = "structural_exit"

LEGACY_VH_SHADOW_FIELDS = [
    "event",
    "symbol",
    "entry_time",
    "exit_time",
    "legacy_open_slots_after",
    "runtime_decision",
]


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


@dataclass
class LegacyVirtualHoldShadow:
    """Shadow tracker for pre-Phase396 virtual-hold slot CAP."""

    cap: int
    open_slots: list[tuple[float, float, str]] = field(default_factory=list)
    accepted_count: int = 0
    rejected_cap_count: int = 0
    peak_slots: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def _prune(self, ent_ts: float) -> None:
        self.open_slots = [(a, b, s) for a, b, s in self.open_slots if b >= ent_ts]

    def simulate(
        self,
        trade: Mapping[str, Any],
        *,
        runtime_decision: str,
    ) -> str:
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        ex = _parse_ts(str(trade.get("exit_time") or "")) or ent + 300.0
        if ent <= 0:
            return "skip"
        self._prune(ent)
        sym = str(trade.get("symbol") or "")
        active = len(self.open_slots)
        self.peak_slots = max(self.peak_slots, active)
        if active >= self.cap:
            self.rejected_cap_count += 1
            outcome = "reject_cap"
        else:
            self.open_slots.append((ent, ex, sym))
            self.accepted_count += 1
            active = len(self.open_slots)
            self.peak_slots = max(self.peak_slots, active)
            outcome = "accept"
        self.events.append(
            {
                "event": f"legacy_vh_{outcome}",
                "symbol": sym,
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "legacy_open_slots_after": active,
                "runtime_decision": runtime_decision,
            }
        )
        return outcome


@dataclass
class PositionCapSessionStats:
    rejected_by_position_cap: int = 0
    position_cap_max_open: int = 0
    observer_open_max_positions: int = 0
    gate_virtual_hold_max_slots: int = 0
    session_close_exit_burst_count: int = 0
    legacy_vh_shadow: Optional[LegacyVirtualHoldShadow] = None

    def record_cap_reject(self) -> None:
        self.rejected_by_position_cap += 1

    def record_observer_open(self, count: int) -> None:
        self.observer_open_max_positions = max(self.observer_open_max_positions, count)
        self.position_cap_max_open = max(self.position_cap_max_open, count)

    def record_gate_vh_slots(self, count: int) -> None:
        self.gate_virtual_hold_max_slots = max(self.gate_virtual_hold_max_slots, count)


def make_position_cap_stats(config: Any) -> Optional[PositionCapSessionStats]:
    if not getattr(config, "position_cap_mode", False):
        return None
    return PositionCapSessionStats(
        legacy_vh_shadow=LegacyVirtualHoldShadow(
            cap=int(getattr(config, "max_concurrent_positions", 3) or 3)
        )
    )


def observer_cap_kwargs(
    observer: Any,
    symbol: str,
) -> dict[str, Any]:
    if observer is None:
        return {"observer_open_count": 0, "observer_symbol_open": False}
    return {
        "observer_open_count": int(observer.open_count()),
        "observer_symbol_open": bool(observer.has_open(symbol)),
    }


def active_cap_positions(
    config: Any,
    *,
    observer: Any,
    gate_open_slots: int,
) -> int:
    if getattr(config, "position_cap_mode", False) and observer is not None:
        return int(observer.open_count())
    return int(gate_open_slots)


def maybe_track_legacy_vh_shadow(
    stats: Optional[PositionCapSessionStats],
    trade: Mapping[str, Any],
    *,
    decision_accept: bool,
    decision_reason: str,
) -> None:
    if stats is None or stats.legacy_vh_shadow is None:
        return
    if not decision_accept and decision_reason != REJECT_MAX_CONCURRENT:
        return
    runtime = "accept" if decision_accept else "reject_cap"
    stats.legacy_vh_shadow.simulate(trade, runtime_decision=runtime)


def count_session_close_exit_burst(events: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for e in events
        if str(e.get("event_type")) == "observer_exit"
        and str(e.get("session_close") or "").lower() in ("true", "1")
    )


def position_cap_summary_fields(
    config: Any,
    state: Any,
    gate: Any,
    *,
    events: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    if not getattr(config, "position_cap_mode", False):
        return {}
    stats: Optional[PositionCapSessionStats] = getattr(state, "position_cap_stats", None)
    legacy = stats.legacy_vh_shadow if stats else None
    accepted = len(getattr(state, "accepted_rows", []) or [])
    legacy_accepted = int(legacy.accepted_count if legacy else 0)
    out: dict[str, Any] = {
        "position_cap_mode": True,
        "position_cap_release": str(
            getattr(config, "position_cap_release", POSITION_CAP_RELEASE_STRUCTURAL_EXIT)
        ),
        "max_concurrent_positions": int(config.max_concurrent_positions),
        "position_cap_max_open": int(stats.position_cap_max_open if stats else 0),
        "observer_open_max_positions": int(
            stats.observer_open_max_positions if stats else getattr(state, "peak_observer_open", 0)
        ),
        "gate_virtual_hold_max_slots": int(
            stats.gate_virtual_hold_max_slots if stats else getattr(state, "peak_open_slots", 0)
        ),
        "accepted_count_position_cap": accepted,
        "rejected_by_position_cap": int(
            stats.rejected_by_position_cap if stats else 0
        ),
        "legacy_virtual_hold_accepted_count_shadow": legacy_accepted,
        "legacy_virtual_hold_delta_accept_count": legacy_accepted - accepted,
        "session_close_exit_burst_count": count_session_close_exit_burst(events or []),
    }
    if legacy is not None:
        out["legacy_virtual_hold_rejected_cap_shadow"] = legacy.rejected_cap_count
        out["legacy_virtual_hold_peak_slots_shadow"] = legacy.peak_slots
    if gate is not None:
        out["open_slots_end"] = len(gate.state.open_slots)
    return out


def write_phase396_artifacts(
    reports_dir: Path,
    *,
    stats: Optional[PositionCapSessionStats],
    summary: Mapping[str, Any],
) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    runtime_json = reports_dir / "phase396_position_cap_runtime_summary.json"
    payload = dict(summary)
    runtime_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out["runtime_summary"] = str(runtime_json)
    if stats and stats.legacy_vh_shadow and stats.legacy_vh_shadow.events:
        shadow_csv = reports_dir / "phase396_legacy_virtual_hold_shadow_events.csv"
        with shadow_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LEGACY_VH_SHADOW_FIELDS, extrasaction="ignore")
            w.writeheader()
            for row in stats.legacy_vh_shadow.events:
                w.writerow(row)
        out["legacy_shadow_csv"] = str(shadow_csv)
    return out
