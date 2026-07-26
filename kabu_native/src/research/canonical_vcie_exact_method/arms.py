"""Incremental arms V1→V2→V3→V4 + D1 — yesterday order only."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_vcie_exact_method.constants import (
    BUY_RATIO_GRID,
    EXPIRY_GRID,
    HOLD_GRID,
    SPREAD_PERCENTILES,
    VOL_RATIO_GRID,
)
from research.canonical_vcie_exact_method.episodes import Episode, build_episodes
from research.canonical_vcie_exact_method.features import context_at, trade_side_at, volume_burst_at
from research.canonical_vcie_exact_method.loader import Tick, exec_ok
from research.canonical_vcie_exact_method.opportunity import Candidate, evaluate_candidates, first_valid_ask, incremental


def _entry_from_ep(ep: Episode, ticks: Sequence[Tick], arm: str) -> Optional[Candidate]:
    if ep.cross_idx is None:
        return None
    # decision at cross; fill E1 = first valid ask after decision (or same if valid)
    fill = first_valid_ask(ticks, ep.cross_idx, min_delay=0.0)
    if fill is None:
        fill = first_valid_ask(ticks, ep.cross_idx, min_delay=0.001)
    if fill is None:
        return None
    idx, ask, _ = fill
    # For V4 prefer hold entry idx
    if arm == "V4_FULL_VCIE":
        if not ep.has_hold or ep.entry_idx is None or not ep.liquidity_ok:
            return None
        fill4 = first_valid_ask(ticks, ep.entry_idx, min_delay=0.0)
        if fill4 is None:
            return None
        idx, ask, _ = fill4
    return Candidate(
        arm=arm,
        day=ep.day,
        symbol=ep.symbol,
        episode_id=ep.episode_id,
        entry_idx=idx,
        entry_time=ticks[idx].ts,
        entry_ask=ask,
        stream_key=ep.stream_key,
        breakout_level=float(ep.breakout_level or 0),
        features={"context_type": ep.context_type, "has_vol": ep.has_volume_before_cross, "has_side": ep.has_side_before_cross, "has_hold": ep.has_hold},
    )


def filter_arm(eps: Sequence[Episode], streams: dict[str, list[Tick]], arm: str) -> list[Candidate]:
    out: list[Candidate] = []
    used = set()
    for ep in eps:
        if ep.episode_id in used:
            continue
        if ep.cross_idx is None:
            continue
        ticks = streams[ep.stream_key]
        ok = False
        if arm == "V1_PRICE_CROSS":
            ok = ep.context_type in ("HOLD", "CONTROLLED_PULLBACK")
        elif arm == "V2_VOLUME_CONFIRMED":
            ok = ep.has_volume_before_cross
        elif arm == "V3_TRADE_SIDE_CONFIRMED":
            ok = ep.has_volume_before_cross and ep.has_side_before_cross
        elif arm == "V4_FULL_VCIE":
            ok = ep.has_volume_before_cross and ep.has_side_before_cross and ep.has_hold and ep.liquidity_ok
        elif arm == "D1_PRICE_PLUS_TRADE_SIDE":
            ok = (not ep.has_volume_before_cross) and ep.has_side_before_cross
        if not ok:
            continue
        c = _entry_from_ep(ep, ticks, arm)
        if c is None:
            continue
        used.add(ep.episode_id)
        out.append(c)
    return out


def build_d1_episodes(
    stream_key: str,
    ticks: Sequence[Tick],
    *,
    buy_ratio: float,
    expiry_sec: float,
) -> list[Episode]:
    """D1: Context + Trade Side + Price Cross without Volume Burst (diagnostic)."""
    # reuse build with very high vol_ratio so burst never triggers; then scan for side+cross
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    ep_n = 0
    i = 20
    while i < len(ticks) - 5:
        ctx = context_at(ticks, i)
        if ctx.get("context_type") not in ("HOLD", "CONTROLLED_PULLBACK"):
            i += 1
            continue
        level = float(ctx["predefined_breakout_level"])
        side_idx = None
        cross_idx = None
        start = i
        j = i
        status = "EXPIRED"
        while j < len(ticks) - 2 and (ticks[j].ts - ticks[start].ts).total_seconds() <= expiry_sec:
            # skip if volume burst present (D1 excludes volume)
            vb = volume_burst_at(ticks, j)
            if vb.get("volume_10s_ratio") is not None and vb["volume_10s_ratio"] >= 1.3:
                status = "FAILED"
                break
            ts = trade_side_at(ticks, j, sec=10.0)
            if side_idx is None and ts.get("aggressive_buy_ratio_10s") is not None:
                if ts["aggressive_buy_ratio_10s"] >= buy_ratio and ts.get("trade_direction_confidence", 0) >= 0.55:
                    side_idx = j
            if side_idx is not None and cross_idx is None:
                px = ticks[j].px
                if px is not None and px > level:
                    cross_idx = j
                    status = "ENTRY_READY"
                    break
            j += 1
        end = min(j, len(ticks) - 1)
        ep_n += 1
        out.append(
            Episode(
                episode_id=f"{day}:{symbol}:VCIE_D1:ep{ep_n}",
                day=day, symbol=symbol, stream_key=stream_key,
                start_idx=start, end_idx=end, start_time=ticks[start].ts, end_time=ticks[end].ts,
                status=status, breakout_level=level, side_idx=side_idx, cross_idx=cross_idx,
                entry_idx=cross_idx, context_type=str(ctx.get("context_type")),
                has_volume_before_cross=False,
                has_side_before_cross=side_idx is not None and cross_idx is not None,
            )
        )
        i = end + 1
    return out


def collect_all_arms(
    streams: dict[str, list[Tick]],
    days: list[str],
    *,
    vol_ratio: float,
    buy_ratio: float,
    hold_mode: str,
    hold_n: float,
    expiry_sec: float,
    spread_max_bps: Optional[float],
) -> dict[str, list[Candidate]]:
    eps_all: list[Episode] = []
    d1_all: list[Episode] = []
    for key, ticks in streams.items():
        if key.split("|")[0] not in days:
            continue
        eps_all.extend(
            build_episodes(
                key, ticks,
                vol_ratio=vol_ratio, buy_ratio=buy_ratio,
                hold_mode=hold_mode, hold_n=hold_n,
                expiry_sec=expiry_sec, spread_max_bps=spread_max_bps,
            )
        )
        d1_all.extend(build_d1_episodes(key, ticks, buy_ratio=buy_ratio, expiry_sec=expiry_sec))
    return {
        "V1_PRICE_CROSS": filter_arm(eps_all, streams, "V1_PRICE_CROSS"),
        "V2_VOLUME_CONFIRMED": filter_arm(eps_all, streams, "V2_VOLUME_CONFIRMED"),
        "V3_TRADE_SIDE_CONFIRMED": filter_arm(eps_all, streams, "V3_TRADE_SIDE_CONFIRMED"),
        "V4_FULL_VCIE": filter_arm(eps_all, streams, "V4_FULL_VCIE"),
        "D1_PRICE_PLUS_TRADE_SIDE": filter_arm(d1_all, streams, "D1_PRICE_PLUS_TRADE_SIDE"),
        "_episodes": eps_all,  # type: ignore
        "_d1_episodes": d1_all,  # type: ignore
    }


def fit_thresholds_train(streams: dict[str, list[Tick]], train_days: list[str]) -> dict[str, Any]:
    """One-factor-at-a-time coarse selection on TRAIN only."""
    # 1) volume ratio
    best_vol = 1.5
    best_score = None
    vol_rows = []
    for vr in VOL_RATIO_GRID:
        arms = collect_all_arms(streams, train_days, vol_ratio=vr, buy_ratio=0.60, hold_mode="events", hold_n=2, expiry_sec=60, spread_max_bps=None)
        v1 = evaluate_candidates(arms["V1_PRICE_CROSS"], streams)
        v2 = evaluate_candidates(arms["V2_VOLUME_CONFIRMED"], streams)
        inc = incremental(v1, v2)
        score = (v2.get("pf") or 0) * 10 + (v2.get("pnl") or 0) / 10000 - (v2.get("never_rate") or 1) * 5
        vol_rows.append({"vol_ratio": vr, "v2": {k: v2.get(k) for k in ("n", "pnl", "pf", "never_rate")}, "inc": inc})
        if best_score is None or score > best_score:
            best_score, best_vol = score, vr

    # 2) trade-side ratio
    best_buy = 0.60
    best_score = None
    buy_rows = []
    for br in BUY_RATIO_GRID:
        arms = collect_all_arms(streams, train_days, vol_ratio=best_vol, buy_ratio=br, hold_mode="events", hold_n=2, expiry_sec=60, spread_max_bps=None)
        v2 = evaluate_candidates(arms["V2_VOLUME_CONFIRMED"], streams)
        v3 = evaluate_candidates(arms["V3_TRADE_SIDE_CONFIRMED"], streams)
        inc = incremental(v2, v3)
        score = (v3.get("pf") or 0) * 10 + (v3.get("pnl") or 0) / 10000
        buy_rows.append({"buy_ratio": br, "v3": {k: v3.get(k) for k in ("n", "pnl", "pf")}, "inc": inc})
        if best_score is None or score > best_score:
            best_score, best_buy = score, br

    # 3) hold
    best_hold = ("events", 2.0)
    best_score = None
    hold_rows = []
    for mode, n in HOLD_GRID:
        arms = collect_all_arms(streams, train_days, vol_ratio=best_vol, buy_ratio=best_buy, hold_mode=mode, hold_n=float(n), expiry_sec=60, spread_max_bps=None)
        v3 = evaluate_candidates(arms["V3_TRADE_SIDE_CONFIRMED"], streams)
        v4 = evaluate_candidates(arms["V4_FULL_VCIE"], streams)
        inc = incremental(v3, v4)
        score = (v4.get("pf") or 0) * 10 + (v4.get("pnl") or 0) / 10000
        hold_rows.append({"hold": (mode, n), "v4": {k: v4.get(k) for k in ("n", "pnl", "pf")}, "inc": inc})
        if best_score is None or score > best_score:
            best_score, best_hold = score, (mode, float(n))

    # 4) expiry
    best_exp = 60
    best_score = None
    exp_rows = []
    for ex in EXPIRY_GRID:
        arms = collect_all_arms(streams, train_days, vol_ratio=best_vol, buy_ratio=best_buy, hold_mode=best_hold[0], hold_n=best_hold[1], expiry_sec=float(ex), spread_max_bps=None)
        v4 = evaluate_candidates(arms["V4_FULL_VCIE"], streams)
        score = (v4.get("pf") or 0) * 10 + (v4.get("pnl") or 0) / 10000
        exp_rows.append({"expiry": ex, "v4_n": v4.get("n"), "v4_pf": v4.get("pf"), "v4_pnl": v4.get("pnl")})
        if best_score is None or score > best_score:
            best_score, best_exp = score, ex

    # 5) spread reject from TRAIN distribution
    spreads = []
    for key, ticks in streams.items():
        if key.split("|")[0] not in train_days:
            continue
        for t in ticks[::50]:
            if t.board.canonical_spread_bps is not None:
                spreads.append(float(t.board.canonical_spread_bps))
    spreads.sort()
    best_spread = None
    spread_rows = []
    if spreads:
        for p in SPREAD_PERCENTILES:
            thr = spreads[int((len(spreads) - 1) * p)]
            arms = collect_all_arms(streams, train_days, vol_ratio=best_vol, buy_ratio=best_buy, hold_mode=best_hold[0], hold_n=best_hold[1], expiry_sec=float(best_exp), spread_max_bps=thr)
            v4 = evaluate_candidates(arms["V4_FULL_VCIE"], streams)
            score = (v4.get("pf") or 0) * 10 + (v4.get("pnl") or 0) / 10000
            spread_rows.append({"p": p, "thr": thr, "v4_n": v4.get("n"), "v4_pf": v4.get("pf")})
            if best_spread is None or score > best_spread[0]:
                best_spread = (score, thr)

    return {
        "vol_ratio": best_vol,
        "buy_ratio": best_buy,
        "hold_mode": best_hold[0],
        "hold_n": best_hold[1],
        "expiry_sec": float(best_exp),
        "spread_max_bps": None if best_spread is None else best_spread[1],
        "diagnostics": {
            "volume": vol_rows,
            "buy": buy_rows,
            "hold": hold_rows,
            "expiry": exp_rows,
            "spread": spread_rows,
        },
    }


def train_gate(v4: dict[str, Any], v1: dict[str, Any], v3: dict[str, Any]) -> tuple[bool, str]:
    if (v4.get("n") or 0) < 30:
        return False, "NO_TRAIN_CANONICAL_VCIE_CANDIDATE:n<30"
    if (v4.get("pnl") or 0) <= 0:
        return False, "NO_TRAIN_CANONICAL_VCIE_CANDIDATE:pnl<=0"
    pf = v4.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1 and pf != float("inf")):
        return False, "NO_TRAIN_CANONICAL_VCIE_CANDIDATE:pf<=1"
    if (v4.get("winner_rate") or 0) <= 0:
        return False, "NO_TRAIN_CANONICAL_VCIE_CANDIDATE:no_winner"
    if v1.get("never_rate") is not None and v4.get("never_rate") is not None:
        if v4["never_rate"] >= v1["never_rate"]:
            return False, "NO_TRAIN_CANONICAL_VCIE_CANDIDATE:never_not_improved"
    if v1.get("early_adverse_rate") is not None and v4.get("early_adverse_rate") is not None:
        if v4["early_adverse_rate"] >= v1["early_adverse_rate"]:
            return False, "NO_TRAIN_CANONICAL_VCIE_CANDIDATE:early_not_improved"
    if (v4.get("top1_symbol_share") or 0) >= 0.40:
        return False, "NO_TRAIN_CANONICAL_VCIE_CANDIDATE:symbol_dep"
    return True, "TRAIN_PASS"


def val_gate(v4: dict[str, Any]) -> tuple[bool, str]:
    if (v4.get("n") or 0) < 5:
        return False, "NO_VALIDATED_CANONICAL_VCIE_CANDIDATE:n"
    if (v4.get("pnl") or 0) <= 0:
        return False, "NO_VALIDATED_CANONICAL_VCIE_CANDIDATE:pnl"
    pf = v4.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1 and pf != float("inf")):
        return False, "NO_VALIDATED_CANONICAL_VCIE_CANDIDATE:pf"
    return True, "VALIDATION_PASS"
