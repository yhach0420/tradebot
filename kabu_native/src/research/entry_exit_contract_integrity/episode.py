"""True episode segmentation (no entry_time in episode_id). Evaluation-only."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Optional, Sequence
from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract_integrity.constants import (
    EC1_LEVEL_TOL_PCT,
    EC2_LEVEL_TOL_PCT,
    EC3_LEVEL_TOL_PCT,
    EPISODE_GAP_END_SEC,
    SAME_WAVE_MAX_GAP_SEC,
)


@dataclass
class EpisodeState:
    episode_id: str
    strategy_id: str
    day: str
    symbol: str
    session: str
    anchor_levels: dict[str, float]
    first_entry_time: datetime
    last_trigger_time: datetime
    peak_high: float
    n_triggers: int = 0
    closed: bool = False
    close_reason: str = ""


def _pct_diff(a: float, b: float) -> float:
    if b == 0:
        return 1e9
    return abs(a - b) / abs(b) * 100.0


def _anchor(c: EntryContract) -> dict[str, float]:
    if c.strategy_id == "EC1":
        return {"breakout_level": float(c.levels.get("breakout_level") or c.invalidation_level)}
    if c.strategy_id == "EC2":
        return {
            "pullback_low": float(c.levels.get("pullback_low") or c.invalidation_level),
            "reclaim_level": float(c.levels.get("reclaim_level") or c.entry_price),
            "pre_pullback_high": float(c.levels.get("pre_pullback_high") or c.entry_price),
        }
    return {
        "range_high": float(c.levels.get("range_high") or c.invalidation_level),
        "range_low": float(c.levels.get("range_low") or 0.0),
    }


def _same_wave(ep: EpisodeState, c: EntryContract) -> bool:
    if ep.closed or ep.strategy_id != c.strategy_id or ep.day != c.day or ep.symbol != c.symbol:
        return False
    if ep.session != c.session:
        return False
    gap = (c.entry_time - ep.last_trigger_time).total_seconds()
    if gap < 0:
        return False
    if gap > SAME_WAVE_MAX_GAP_SEC:
        return False
    a = _anchor(c)
    if c.strategy_id == "EC1":
        bl = a["breakout_level"]
        ebl = ep.anchor_levels["breakout_level"]
        # same breakout neighborhood OR continuation above prior breakout with slight level drift
        if _pct_diff(bl, ebl) <= EC1_LEVEL_TOL_PCT:
            return True
        if bl <= ep.peak_high * 1.002 and bl >= ebl * (1 - EC1_LEVEL_TOL_PCT / 100.0):
            return True
        return False
    if c.strategy_id == "EC2":
        if _pct_diff(a["pullback_low"], ep.anchor_levels["pullback_low"]) <= EC2_LEVEL_TOL_PCT:
            if _pct_diff(a["reclaim_level"], ep.anchor_levels["reclaim_level"]) <= EC2_LEVEL_TOL_PCT * 1.5:
                return True
        # same pullback low family
        return _pct_diff(a["pullback_low"], ep.anchor_levels["pullback_low"]) <= EC2_LEVEL_TOL_PCT * 0.5
    # EC3
    if _pct_diff(a["range_high"], ep.anchor_levels["range_high"]) <= EC3_LEVEL_TOL_PCT:
        if _pct_diff(a["range_low"], ep.anchor_levels["range_low"]) <= EC3_LEVEL_TOL_PCT * 2:
            return True
    return False


def _should_close(ep: EpisodeState, c: EntryContract) -> bool:
    """Close prior episode before starting a clearly new wave."""
    gap = (c.entry_time - ep.last_trigger_time).total_seconds()
    if gap >= EPISODE_GAP_END_SEC and not _same_wave(ep, c):
        return True
    a = _anchor(c)
    if c.strategy_id == "EC1":
        # new distinctly higher breakout after gap → new impulse wave
        if gap >= 120 and a["breakout_level"] > ep.anchor_levels["breakout_level"] * (1 + EC1_LEVEL_TOL_PCT / 100.0 * 2):
            return True
    if c.strategy_id == "EC2":
        if gap >= 120 and _pct_diff(a["pullback_low"], ep.anchor_levels["pullback_low"]) > EC2_LEVEL_TOL_PCT:
            return True
    if c.strategy_id == "EC3":
        if gap >= 120 and _pct_diff(a["range_high"], ep.anchor_levels["range_high"]) > EC3_LEVEL_TOL_PCT:
            return True
    return False


def _new_episode_id(c: EntryContract, seq: int) -> str:
    """Structural episode id — no entry_time."""
    a = _anchor(c)
    if c.strategy_id == "EC1":
        key = f"{a['breakout_level']:.4f}"
    elif c.strategy_id == "EC2":
        key = f"{a['pullback_low']:.4f}_{a['reclaim_level']:.4f}"
    else:
        key = f"{a['range_high']:.4f}_{a['range_low']:.4f}"
    return f"{c.strategy_id}:{c.day}:{c.symbol}:{c.session}:{key}:e{seq}"


def segment_true_episodes(contracts: Sequence[EntryContract]) -> dict[str, Any]:
    """Assign true episode_ids and select one-entry-per-episode (first chronologically)."""
    by_key: dict[tuple, list[EntryContract]] = {}
    for c in contracts:
        by_key.setdefault((c.strategy_id, c.day, c.symbol, c.session), []).append(c)

    remapped: list[EntryContract] = []
    accepted: list[EntryContract] = []
    blocked_rows: list[dict[str, Any]] = []
    episode_meta: list[dict[str, Any]] = []
    seq_global = 0

    for key, xs in by_key.items():
        xs = sorted(xs, key=lambda c: (c.entry_time, c.setup_id))
        open_ep: Optional[EpisodeState] = None
        local_seq = 0
        for c in xs:
            if open_ep is not None and _should_close(open_ep, c):
                open_ep.closed = True
                open_ep.close_reason = "gap_or_new_structure"
                episode_meta.append(
                    {
                        "episode_id": open_ep.episode_id,
                        "strategy_id": open_ep.strategy_id,
                        "day": open_ep.day,
                        "symbol": open_ep.symbol,
                        "n_triggers": open_ep.n_triggers,
                        "close_reason": open_ep.close_reason,
                    }
                )
                open_ep = None

            if open_ep is not None and _same_wave(open_ep, c):
                open_ep.n_triggers += 1
                open_ep.last_trigger_time = c.entry_time
                open_ep.peak_high = max(open_ep.peak_high, c.entry_price)
                c2 = replace(c, episode_id=open_ep.episode_id)
                remapped.append(c2)
                blocked_rows.append(
                    {
                        "setup_id": c.setup_id,
                        "episode_id": open_ep.episode_id,
                        "reason": "SAME_WAVE_REENTRY",
                        "strategy_id": c.strategy_id,
                        "day": c.day,
                        "symbol": c.symbol,
                        "entry_time": c.entry_time.isoformat(),
                    }
                )
                continue

            # new episode — first entry accepted
            local_seq += 1
            seq_global += 1
            eid = _new_episode_id(c, local_seq)
            open_ep = EpisodeState(
                episode_id=eid,
                strategy_id=c.strategy_id,
                day=c.day,
                symbol=c.symbol,
                session=c.session,
                anchor_levels=_anchor(c),
                first_entry_time=c.entry_time,
                last_trigger_time=c.entry_time,
                peak_high=c.entry_price,
                n_triggers=1,
            )
            c2 = replace(c, episode_id=eid)
            remapped.append(c2)
            accepted.append(c2)

        if open_ep is not None:
            episode_meta.append(
                {
                    "episode_id": open_ep.episode_id,
                    "strategy_id": open_ep.strategy_id,
                    "day": open_ep.day,
                    "symbol": open_ep.symbol,
                    "n_triggers": open_ep.n_triggers,
                    "close_reason": "end_of_stream",
                }
            )

    raw_n = len(contracts)
    ep_n = len({c.episode_id for c in remapped})
    same_sym_re = sum(1 for r in blocked_rows if r["reason"] == "SAME_WAVE_REENTRY")
    return {
        "raw_trigger_n": raw_n,
        "true_episode_n": ep_n,
        "trigger_per_episode": round(raw_n / ep_n, 4) if ep_n else None,
        "one_episode_one_entry_n": len(accepted),
        "episode_blocked_n": len(blocked_rows),
        "same_symbol_reentry_n": same_sym_re,  # blocked same-wave on same symbol
        "same_wave_reentry_n": same_sym_re,
        "remapped": remapped,
        "accepted": accepted,
        "blocked": blocked_rows,
        "episode_meta": episode_meta,
        "verdict": "TRUE_EPISODE_SEGMENTATION_READY" if ep_n > 0 else "TRUE_EPISODE_SEGMENTATION_BLOCKED",
    }
