"""Post-ENTRY exit episode state machine — classify A–E, emit exit signals."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_fcr_exact_method.loader import Tick
from research.canonical_fcr_exact_method.observations import window_flow
from research.canonical_fcr_exit_episode.constants import (
    GIVEBACK_FRAC,
    HORIZON_SEC,
    NO_PROGRESS_SEC,
    NOISE_ADVERSE_PCT,
    WINNER_MFE_PCT,
)
from research.canonical_fcr_exit_episode.entry_fixed import FrozenEntry

# Entry → ADVANCE → branches (no skip)
POST_STATES = (
    "ENTRY", "ADVANCE",
    "HEALTHY_ADVANCE", "TEMPORARY_NOISE", "FALSE_RECLAIM", "NO_PROGRESS", "WINNER_GIVEBACK",
)


@dataclass
class ExitEpisode:
    entry: FrozenEntry
    states: list[str] = field(default_factory=list)
    terminal_class: str = "ADVANCE"  # one of A–E labels
    # first times observed
    t_healthy: Optional[datetime] = None
    t_noise: Optional[datetime] = None
    t_false: Optional[datetime] = None
    t_noprogress: Optional[datetime] = None
    t_giveback: Optional[datetime] = None
    # exit signal indices (None = not triggered)
    idx_false_reclaim: Optional[int] = None
    idx_structure: Optional[int] = None
    idx_noprogress: Optional[int] = None
    idx_giveback: Optional[int] = None
    idx_horizon: Optional[int] = None
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    post_high: Optional[float] = None
    features: dict[str, Any] = field(default_factory=dict)


def _ret(bid: float, entry_ask: float) -> float:
    return (bid - entry_ask) / entry_ask * 100.0 if entry_ask > 0 else 0.0


def build_exit_episode(entry: FrozenEntry, ticks: Sequence[Tick]) -> ExitEpisode:
    """Causal post-entry walk: ADVANCE then classify without state skip."""
    ep = ExitEpisode(entry=entry, states=["ENTRY", "ADVANCE"])
    i0 = entry.entry_idx
    if i0 >= len(ticks) - 1:
        ep.terminal_class = "NO_PROGRESS"
        ep.states.append("NO_PROGRESS")
        return ep

    t0 = ticks[i0].ts
    ask = entry.entry_ask
    reclaim = entry.reclaim_level
    pb_low = entry.pullback_low
    post_high = ask
    post_low = ask
    mfe = mae = 0.0
    saw_hh = False
    saw_healthy = False
    saw_noise = False
    mfe_peak = 0.0
    last_hh_ts = t0
    j = i0 + 1
    # ADVANCE first event required before branching
    advanced = False

    while j < len(ticks):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if dt > HORIZON_SEC + 5:
            break
        bid = t.board.canonical_best_bid
        px = t.px
        if bid is None or bid <= 0:
            j += 1
            continue
        if not advanced:
            advanced = True  # first post-entry observation = ADVANCE done

        ret = _ret(float(bid), ask)
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        mfe_peak = max(mfe_peak, mfe)
        if px is not None:
            if px > post_high:
                post_high = px
                saw_hh = True
                last_hh_ts = t.ts
            post_low = min(post_low, px)

        fl10 = window_flow(ticks, j, 10)
        fl30 = window_flow(ticks, j, 30)
        buy_gone = fl10["buy_ratio"] < 0.45 and fl10["buy_v"] <= fl30["buy_v"] * 0.5 + 1e-9
        sell_up = fl10["sell_v"] > fl30["sell_v"] * 0.55 + 1e-9 and fl10["sell_n"] >= 2
        spread_bps = t.board.canonical_spread_bps
        spread_bad = spread_bps is not None and spread_bps > 40

        # --- False Reclaim (C) ---
        reclaim_break = px is not None and px < reclaim
        if reclaim_break and (buy_gone or sell_up or spread_bad):
            if ep.idx_false_reclaim is None:
                ep.idx_false_reclaim = j
                ep.t_false = t.ts
                if "FALSE_RECLAIM" not in ep.states:
                    ep.states.append("FALSE_RECLAIM")
                ep.terminal_class = "FALSE_RECLAIM"

        # --- Structure: pullback low / lower low ---
        struct_break = False
        if pb_low is not None and px is not None and px < pb_low:
            struct_break = True
        if px is not None and px < post_low * 0.999 and mae <= -0.35:
            # lower low vs early post-entry range
            if j > i0 + 3:
                struct_break = True
        if struct_break and ep.idx_structure is None:
            ep.idx_structure = j

        # --- No Progress (D) ---
        if dt >= NO_PROGRESS_SEC and (not saw_hh) and mfe < 0.15 and ret <= 0.05:
            if ep.idx_noprogress is None:
                ep.idx_noprogress = j
                ep.t_noprogress = t.ts
                if "NO_PROGRESS" not in ep.states:
                    ep.states.append("NO_PROGRESS")
                if ep.terminal_class not in ("FALSE_RECLAIM", "WINNER_GIVEBACK"):
                    ep.terminal_class = "NO_PROGRESS"

        # --- Temporary Noise (B) — adverse but structure held ---
        noise = (
            ret < 0 and abs(ret) <= NOISE_ADVERSE_PCT
            and (pb_low is None or (px is not None and px >= pb_low))
            and (px is None or px >= reclaim)
            and fl10["buy_ratio"] >= 0.50
            and not spread_bad
        )
        if noise and not saw_noise:
            saw_noise = True
            ep.t_noise = t.ts
            if "TEMPORARY_NOISE" not in ep.states:
                ep.states.append("TEMPORARY_NOISE")

        # --- Healthy Advance (A) ---
        healthy = (
            saw_hh and ret > 0
            and fl10["buy_ratio"] >= 0.55
            and not sell_up
            and (pb_low is None or (px is not None and px >= pb_low))
            and (px is None or px >= reclaim)
        )
        if healthy and not saw_healthy:
            saw_healthy = True
            ep.t_healthy = t.ts
            if "HEALTHY_ADVANCE" not in ep.states:
                ep.states.append("HEALTHY_ADVANCE")
            if ep.terminal_class in ("ADVANCE", "TEMPORARY_NOISE", "NO_PROGRESS"):
                ep.terminal_class = "HEALTHY_ADVANCE"

        # after noise, can recover to healthy
        if saw_noise and healthy and ep.terminal_class == "TEMPORARY_NOISE":
            ep.terminal_class = "HEALTHY_ADVANCE"

        # --- Winner Giveback (E) ---
        stalled = (t.ts - last_hh_ts).total_seconds() >= 20 and saw_hh
        giving = mfe_peak >= WINNER_MFE_PCT and ret < mfe_peak * (1 - GIVEBACK_FRAC) and ret > 0
        flow_fade = fl10["buy_ratio"] < 0.50 or sell_up
        if mfe_peak >= WINNER_MFE_PCT and stalled and giving and flow_fade:
            if ep.idx_giveback is None:
                ep.idx_giveback = j
                ep.t_giveback = t.ts
                if "WINNER_GIVEBACK" not in ep.states:
                    ep.states.append("WINNER_GIVEBACK")
                ep.terminal_class = "WINNER_GIVEBACK"

        if dt >= HORIZON_SEC:
            ep.idx_horizon = j
            break
        j += 1

    if ep.idx_horizon is None:
        # last available within horizon
        for k in range(min(len(ticks) - 1, i0 + 1), len(ticks)):
            if (ticks[k].ts - t0).total_seconds() >= HORIZON_SEC:
                ep.idx_horizon = k
                break
        if ep.idx_horizon is None:
            ep.idx_horizon = len(ticks) - 1

    ep.mfe_pct = mfe
    ep.mae_pct = mae
    ep.post_high = post_high
    ep.features = {
        "saw_hh": saw_hh, "saw_healthy": saw_healthy, "saw_noise": saw_noise,
        "mfe": mfe, "mae": mae,
    }
    # default terminal if still ADVANCE
    if ep.terminal_class == "ADVANCE":
        if saw_healthy:
            ep.terminal_class = "HEALTHY_ADVANCE"
        elif saw_noise:
            ep.terminal_class = "TEMPORARY_NOISE"
        else:
            ep.terminal_class = "NO_PROGRESS"
            if "NO_PROGRESS" not in ep.states:
                ep.states.append("NO_PROGRESS")
    return ep


def class_counts(episodes: Sequence[ExitEpisode]) -> dict[str, int]:
    keys = ("HEALTHY_ADVANCE", "TEMPORARY_NOISE", "FALSE_RECLAIM", "NO_PROGRESS", "WINNER_GIVEBACK", "ADVANCE")
    out = {k: 0 for k in keys}
    for e in episodes:
        out[e.terminal_class] = out.get(e.terminal_class, 0) + 1
    return out
