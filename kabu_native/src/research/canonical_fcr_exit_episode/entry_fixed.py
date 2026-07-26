"""Frozen FCR ENTRY — no ENTRY retune / no condition change."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from research.canonical_fcr_exact_method.loader import Tick, load_streams
from research.canonical_fcr_exact_method.opportunity import first_valid_ask
from research.canonical_fcr_exact_method.state_machine import Episode, build_episodes
from research.canonical_fcr_exit_episode.constants import FROZEN_ENTRY, STRIDE


@dataclass
class FrozenEntry:
    day: str
    symbol: str
    stream_key: str
    episode_id: str
    impulse_id: str
    entry_idx: int
    entry_time: datetime
    entry_ask: float
    reclaim_level: float
    pullback_low: Optional[float]
    impulse_high: Optional[float]


def collect_frozen_entries(
    streams: dict[str, list[Tick]],
    days: list[str],
) -> list[FrozenEntry]:
    """F5 FULL FCR ENTRY_READY only — frozen SM + frozen thresholds."""
    p = FROZEN_ENTRY
    out: list[FrozenEntry] = []
    used_imp: set[str] = set()
    for key, ticks in streams.items():
        if key.split("|")[0] not in days:
            continue
        eps = build_episodes(
            key, ticks,
            slope_min=p["slope_min"],
            pb_frac_lo=p["pb_lo"],
            pb_frac_hi=p["pb_hi"],
            new_low_stop_sec=p["new_low_stop_sec"],
            buy_ratio=p["buy_ratio"],
            freq_accel=p["freq_accel"],
            reclaim_hold_events=p["reclaim_hold_events"],
            expiry_exh_to_buy=p["expiry_exh_to_buy"],
            expiry_buy_to_reclaim=p["expiry_buy_to_reclaim"],
            spread_max_bps=p["spread_max_bps"],
        )
        for ep in eps:
            if ep.status != "ENTRY_READY" or ep.entry_idx is None:
                continue
            if not (ep.flags.get("has_trend") and ep.flags.get("has_pullback")
                    and ep.flags.get("has_exhaustion") and ep.flags.get("has_buy_flow")
                    and ep.flags.get("has_reclaim") and ep.flags.get("liq_ok")):
                continue
            if ep.reclaim_level is None:
                continue
            if ep.impulse_id in used_imp:
                continue
            fill = first_valid_ask(ticks, ep.entry_idx, min_delay=0.0)
            if fill is None:
                fill = first_valid_ask(ticks, ep.entry_idx, min_delay=0.001)
            if fill is None:
                continue
            idx, ask, _ = fill
            used_imp.add(ep.impulse_id)
            out.append(FrozenEntry(
                day=ep.day, symbol=ep.symbol, stream_key=key,
                episode_id=ep.episode_id, impulse_id=ep.impulse_id,
                entry_idx=idx, entry_time=ticks[idx].ts, entry_ask=ask,
                reclaim_level=float(ep.reclaim_level),
                pullback_low=ep.pullback_low,
                impulse_high=ep.impulse_high,
            ))
    out.sort(key=lambda e: (e.day, e.entry_time, e.symbol))
    return out


def load_for_exit(days: list[str]) -> dict[str, list[Tick]]:
    return load_streams(days, stride=STRIDE)
