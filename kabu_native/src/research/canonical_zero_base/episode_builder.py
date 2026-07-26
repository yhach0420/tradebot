"""True episode construction — episode_id never includes entry timestamp."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_zero_base.canonical_loader import Tick
from research.canonical_zero_base.feature_library import features_at


def _session(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


@dataclass
class EpisodeEvent:
    tick: Tick
    episode_id: str
    features: dict[str, Any]
    setup: str  # impulse|pullback|compression|breakout|absorption|none


def build_episodes(ticks: Sequence[Tick]) -> list[EpisodeEvent]:
    if not ticks:
        return []
    out: list[EpisodeEvent] = []
    ep_n = 0
    ep_start_px = ticks[0].px
    ep_start_ts = ticks[0].ts
    setup = "none"
    last_ts = ticks[0].ts
    cur_id = f"{ticks[0].day}:{ticks[0].symbol}:ep{ep_n}"

    for i, t in enumerate(ticks):
        feat = features_at(ticks, i)
        gap = (t.ts - last_ts).total_seconds() > 120
        sess_flip = _session(t.ts) != _session(last_ts)
        reset = abs(t.px - ep_start_px) / ep_start_px > 0.02 if ep_start_px > 0 else False
        age = (t.ts - ep_start_ts).total_seconds()
        max_horizon = age > 1800

        # classify setup from past features
        new_setup = setup
        r30 = feat.get("return_30s")
        dry = feat.get("volume_dryup")
        comp = feat.get("compression_ratio")
        dist = feat.get("distance_from_high")
        ask_dep = feat.get("ask_depletion")
        if r30 is not None and r30 > 0.003:
            new_setup = "impulse"
        if feat.get("drawdown_from_high") is not None and feat["drawdown_from_high"] > 0.002 and r30 is not None and r30 < 0:
            new_setup = "pullback"
        if dist is not None and dist < 0.001 and (feat.get("volume_vs_recent_baseline") or 0) > 1.2:
            new_setup = "breakout"
        if ask_dep and (feat.get("canonical_ask_qty") or 0) > 0 and (feat.get("bounce_from_low") or 0) >= 0:
            if (feat.get("canonical_ask_qty") or 0) >= (feat.get("canonical_bid_qty") or 0):
                new_setup = "absorption"
        if comp is not None and comp < 0.4 and dry:
            new_setup = "compression"

        end = gap or sess_flip or max_horizon or (reset and new_setup != setup and setup != "none")
        if end and out:
            ep_n += 1
            cur_id = f"{t.day}:{t.symbol}:ep{ep_n}"
            ep_start_px = t.px
            ep_start_ts = t.ts
            setup = "none"

        if new_setup != "none" and (setup == "none" or new_setup != setup):
            # new independent setup within stream → new episode (no entry ts in id)
            if setup != "none" and new_setup != setup:
                ep_n += 1
                cur_id = f"{t.day}:{t.symbol}:ep{ep_n}"
                ep_start_px = t.px
                ep_start_ts = t.ts
            setup = new_setup

        feat["episode_age_sec"] = (t.ts - ep_start_ts).total_seconds()
        feat["episode_price_progress"] = (t.px - ep_start_px) / ep_start_px if ep_start_px else None
        feat["reset_confirmed"] = reset
        feat["data_gap"] = gap
        out.append(EpisodeEvent(tick=t, episode_id=cur_id, features=feat, setup=setup))
        last_ts = t.ts
    return out


def episode_stats(events: Sequence[EpisodeEvent]) -> dict[str, Any]:
    ids = {e.episode_id for e in events}
    by = {}
    for e in events:
        by.setdefault(e.episode_id, []).append(e)
    durs = []
    for rows in by.values():
        durs.append((rows[-1].tick.ts - rows[0].tick.ts).total_seconds())
    return {
        "raw_events": len(events),
        "true_episodes": len(ids),
        "events_per_episode": (len(events) / len(ids)) if ids else None,
        "median_episode_sec": sorted(durs)[len(durs) // 2] if durs else None,
        "setup_counts": {s: sum(1 for e in events if e.setup == s) for s in ("impulse", "pullback", "breakout", "absorption", "compression", "none")},
    }
