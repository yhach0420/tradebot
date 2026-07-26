"""Simulate fixed EXIT modes on FixedEntry cohorts (rules unchanged)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.exit_rules import ExitParams, simulate_exit
from research.price_flow_exit.path_mfe import bars_after_entry, simulate_x0
from research.price_flow_exit_integrity.constants import PATH_MAX_SEC
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds


@dataclass
class SimTrade:
    day: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_5bps: float
    hold_sec: float
    entry_method: str
    cohort: str
    setup_id: str
    impulse_episode_id: str
    breakout_episode_id: str
    pbv2: bool
    vcie: bool
    mode: str
    session: str  # AM|PM

    def to_row(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "symbol": self.symbol,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl_5bps": self.pnl_5bps,
            "hold_sec": self.hold_sec,
            "entry_method": self.entry_method,
            "cohort": self.cohort,
            "setup_id": self.setup_id,
            "impulse_episode_id": self.impulse_episode_id,
            "breakout_episode_id": self.breakout_episode_id,
            "pbv2": self.pbv2,
            "vcie": self.vcie,
            "mode": self.mode,
            "session": self.session,
        }


def _session_of(t: datetime) -> str:
    return "AM" if t.hour < 12 else "PM"


def simulate_trades(
    entries: Sequence[FixedEntry],
    push_by_day: dict[str, dict],
    *,
    mode: str,
    params: ExitParams,
    bars_cache: Optional[dict[tuple[str, str], list]] = None,
) -> list[SimTrade]:
    bars_cache = bars_cache if bars_cache is not None else {}
    out: list[SimTrade] = []
    for e in entries:
        ticks = (push_by_day.get(e.day) or {}).get(e.symbol) or []
        if not ticks:
            continue
        key = (e.day, e.symbol)
        if key not in bars_cache:
            bars_cache[key] = aggregate_to_seconds(ticks)
        path = bars_after_entry(bars_cache[key], e.entry_time, max_sec=PATH_MAX_SEC)
        if not path:
            continue
        ex = simulate_x0(e, path) if mode == "X0" else simulate_exit(e, path, mode=mode, params=params)
        out.append(
            SimTrade(
                day=e.day,
                symbol=e.symbol,
                entry_time=e.entry_time,
                exit_time=ex.exit_time,
                entry_price=e.entry_price,
                exit_price=ex.exit_price,
                exit_reason=ex.exit_reason,
                pnl_5bps=float(ex.pnl_5bps),
                hold_sec=float(ex.hold_sec),
                entry_method=e.entry_method,
                cohort=e.cohort,
                setup_id=e.setup_id,
                impulse_episode_id=e.impulse_episode_id or e.setup_id,
                breakout_episode_id=e.breakout_episode_id or e.setup_id,
                pbv2=bool(e.pbv2),
                vcie=bool(e.vcie),
                mode=mode,
                session=_session_of(e.entry_time),
            )
        )
    return out
