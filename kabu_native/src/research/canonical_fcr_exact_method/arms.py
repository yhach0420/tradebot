"""F0→F5 incremental arms + D1/D2 diagnostics."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_fcr_exact_method.constants import (
    BUY_RATIO_GRID,
    EXPIRY_BUY_TO_RECLAIM,
    EXPIRY_EXH_TO_BUY,
    NEW_LOW_STOP_SEC,
    PULLBACK_FRAC,
    RECLAIM_HOLD,
    SPREAD_PERCENTILES,
)
from research.canonical_fcr_exact_method.loader import Tick
from research.canonical_fcr_exact_method.observations import trend_context
from research.canonical_fcr_exact_method.opportunity import Candidate, evaluate_candidates, first_valid_ask, increment_effect
from research.canonical_fcr_exact_method.state_machine import Episode, build_episodes, build_f0_episodes


def _cand(ep: Episode, ticks: Sequence[Tick], arm: str, decision_idx: int) -> Optional[Candidate]:
    fill = first_valid_ask(ticks, decision_idx, min_delay=0.0)
    if fill is None:
        fill = first_valid_ask(ticks, decision_idx, min_delay=0.001)
    if fill is None:
        return None
    idx, ask, _ = fill
    return Candidate(
        arm=arm, day=ep.day, symbol=ep.symbol, episode_id=ep.episode_id,
        impulse_id=ep.impulse_id, entry_idx=idx, entry_time=ticks[idx].ts,
        entry_ask=ask, stream_key=ep.stream_key, features=dict(ep.flags),
    )


def filter_arm(
    eps: Sequence[Episode],
    streams: dict[str, list[Tick]],
    arm: str,
    *,
    slope_min: float = 0.0,
) -> list[Candidate]:
    out: list[Candidate] = []
    used_ep: set[str] = set()
    used_imp: set[str] = set()
    for ep in eps:
        if ep.episode_id in used_ep or ep.impulse_id in used_imp:
            continue
        ticks = streams[ep.stream_key]
        f = ep.flags
        ok = False
        dec = None
        if arm == "F0_RECLAIM_ONLY":
            ok = ep.reclaim_idx is not None or ep.entry_idx is not None
            dec = ep.entry_idx or ep.reclaim_idx
        elif arm == "F1_TREND_RECLAIM":
            # F0 reclaim + trend context at decision (not full FCR path)
            dec = ep.entry_idx or ep.reclaim_idx
            if dec is not None:
                tr = trend_context(ticks, dec, slope_min=slope_min)
                ok = bool(tr.get("ok"))
        elif arm == "F2_PULLBACK_RECLAIM":
            # trend + normal pullback + micro-high reclaim (exhaustion not required)
            ok = f.get("has_trend") and f.get("has_pullback") and (
                f.get("d1_reclaim") or f.get("d2_reclaim") or f.get("has_reclaim")
            )
            dec = ep.d1_reclaim_idx or ep.d2_reclaim_idx or ep.reclaim_idx or ep.entry_idx
        elif arm == "F3_SELLING_EXHAUSTED":
            # F2 + selling exhaustion; reclaim after exhaust (may be before buy flow)
            ok = (
                f.get("has_trend") and f.get("has_pullback") and f.get("has_exhaustion")
                and (f.get("d2_reclaim") or f.get("has_reclaim"))
            )
            dec = ep.d2_reclaim_idx or ep.reclaim_idx or ep.entry_idx
        elif arm == "F4_BUY_FLOW_CONFIRMED":
            ok = f.get("has_trend") and f.get("has_pullback") and f.get("has_exhaustion") and f.get("has_buy_flow")
            # entry at buy confirm (price bounce) — use buy_idx
            dec = ep.buy_idx
        elif arm == "F5_FULL_FCR":
            ok = (
                f.get("has_trend") and f.get("has_pullback") and f.get("has_exhaustion")
                and f.get("has_buy_flow") and f.get("has_reclaim") and ep.status == "ENTRY_READY"
                and f.get("liq_ok")
            )
            dec = ep.entry_idx
        elif arm == "D1_NO_EXHAUSTION":
            ok = f.get("has_trend") and f.get("has_pullback") and f.get("d1_reclaim") and ep.d1_reclaim_idx is not None
            dec = ep.d1_reclaim_idx
        elif arm == "D2_NO_BUY_FLOW":
            ok = (
                f.get("has_trend") and f.get("has_pullback") and f.get("has_exhaustion")
                and f.get("d2_reclaim") and ep.d2_reclaim_idx is not None
            )
            dec = ep.d2_reclaim_idx
        if not ok or dec is None:
            continue
        c = _cand(ep, ticks, arm, dec)
        if c is None:
            continue
        used_ep.add(ep.episode_id)
        used_imp.add(ep.impulse_id)
        out.append(c)
    return out


def collect_arms(
    streams: dict[str, list[Tick]],
    days: list[str],
    *,
    params: dict[str, Any],
    include_f0: bool = True,
) -> dict[str, Any]:
    eps: list[Episode] = []
    f0: list[Episode] = []
    slope_min = float(params.get("slope_min", 0.0))
    for key, ticks in streams.items():
        if key.split("|")[0] not in days:
            continue
        eps.extend(build_episodes(
            key, ticks,
            slope_min=slope_min,
            pb_frac_lo=params["pb_lo"],
            pb_frac_hi=params["pb_hi"],
            new_low_stop_sec=params["new_low_stop_sec"],
            buy_ratio=params["buy_ratio"],
            freq_accel=params.get("freq_accel", 1.5),
            reclaim_hold_events=params["reclaim_hold_events"],
            expiry_exh_to_buy=params["expiry_exh_to_buy"],
            expiry_buy_to_reclaim=params["expiry_buy_to_reclaim"],
            spread_max_bps=params.get("spread_max_bps"),
        ))
        if include_f0:
            f0.extend(build_f0_episodes(key, ticks))
    out: dict[str, Any] = {
        "F2_PULLBACK_RECLAIM": filter_arm(eps, streams, "F2_PULLBACK_RECLAIM", slope_min=slope_min),
        "F3_SELLING_EXHAUSTED": filter_arm(eps, streams, "F3_SELLING_EXHAUSTED", slope_min=slope_min),
        "F4_BUY_FLOW_CONFIRMED": filter_arm(eps, streams, "F4_BUY_FLOW_CONFIRMED", slope_min=slope_min),
        "F5_FULL_FCR": filter_arm(eps, streams, "F5_FULL_FCR", slope_min=slope_min),
        "D1_NO_EXHAUSTION": filter_arm(eps, streams, "D1_NO_EXHAUSTION", slope_min=slope_min),
        "D2_NO_BUY_FLOW": filter_arm(eps, streams, "D2_NO_BUY_FLOW", slope_min=slope_min),
        "_episodes": eps,
        "_f0": f0,
    }
    if include_f0:
        out["F0_RECLAIM_ONLY"] = filter_arm(f0, streams, "F0_RECLAIM_ONLY", slope_min=slope_min)
        out["F1_TREND_RECLAIM"] = filter_arm(f0, streams, "F1_TREND_RECLAIM", slope_min=slope_min)
    else:
        out["F0_RECLAIM_ONLY"] = []
        out["F1_TREND_RECLAIM"] = []
    return out


def _score(ev: dict[str, Any]) -> float:
    n = int(ev.get("n") or 0)
    if n <= 0:
        return -1e9
    # prefer evaluable sample size; never pick a zero-n "best" threshold
    return (
        min(n, 40) * 0.15
        + (ev.get("pf") or 0) * 10
        + (ev.get("mean") or 0) / 1000
        - (ev.get("never_rate") or 1) * 3
    )


def _fit_streams(streams: dict[str, list[Tick]], train_days: list[str], *, max_keys: int = 40) -> dict[str, list[Tick]]:
    """Deterministic TRAIN subsample for coarse threshold fit only."""
    keys = sorted(k for k in streams if k.split("|")[0] in train_days)
    if len(keys) <= max_keys:
        return {k: streams[k] for k in keys}
    step = max(1, len(keys) // max_keys)
    picked = keys[::step][:max_keys]
    return {k: streams[k] for k in picked}


def fit_thresholds_train(streams: dict[str, list[Tick]], train_days: list[str]) -> dict[str, Any]:
    """One-factor-at-a-time coarse TRAIN selection (F5-only scoring on TRAIN subsample)."""
    fit_s = _fit_streams(streams, train_days, max_keys=30)
    base = {
        "slope_min": 0.0,
        "pb_lo": 0.10,
        "pb_hi": 0.50,
        "new_low_stop_sec": 20.0,
        "buy_ratio": 0.60,
        "freq_accel": 1.5,
        "reclaim_hold_events": 2,
        "expiry_exh_to_buy": 20.0,
        "expiry_buy_to_reclaim": 10.0,
        "spread_max_bps": None,
    }
    diag: dict[str, Any] = {"fit_stream_n": len(fit_s)}

    def _eval_f5(p: dict[str, Any]) -> dict[str, Any]:
        arms = collect_arms(fit_s, train_days, params=p, include_f0=False)
        return evaluate_candidates(arms["F5_FULL_FCR"], fit_s)

    # 1 pullback depth
    best = dict(base)
    best_s = None
    rows = []
    for lo, hi in PULLBACK_FRAC:
        p = dict(base, pb_lo=lo, pb_hi=hi)
        f5 = _eval_f5(p)
        s = _score(f5)
        rows.append({"pb": (lo, hi), "f5_n": f5.get("n"), "f5_pf": f5.get("pf")})
        if best_s is None or s > best_s:
            best_s, best = s, p
    diag["pullback"] = rows

    # 2 selling exhaustion timing
    best_s = None
    rows = []
    for sec in NEW_LOW_STOP_SEC:
        p = dict(best, new_low_stop_sec=float(sec))
        f5 = _eval_f5(p)
        s = _score(f5)
        rows.append({"new_low_stop": sec, "f5_n": f5.get("n"), "f5_pf": f5.get("pf")})
        if best_s is None or s > best_s:
            best_s, best = s, p
    diag["exhaustion"] = rows

    # 3 buy ratio
    best_s = None
    rows = []
    for br in BUY_RATIO_GRID:
        p = dict(best, buy_ratio=br)
        f5 = _eval_f5(p)
        s = _score(f5)
        rows.append({"buy_ratio": br, "f5_n": f5.get("n"), "f5_pf": f5.get("pf")})
        if best_s is None or s > best_s:
            best_s, best = s, p
    diag["buy"] = rows

    # 4 reclaim hold
    best_s = None
    rows = []
    for mode, n in RECLAIM_HOLD:
        p = dict(best, reclaim_hold_events=0 if mode == "cross" else int(n))
        f5 = _eval_f5(p)
        s = _score(f5)
        rows.append({"hold": (mode, n), "f5_n": f5.get("n"), "f5_pf": f5.get("pf")})
        if best_s is None or s > best_s:
            best_s, best = s, p
    diag["reclaim"] = rows

    # 5 expiry — one factor at a time (no cartesian grid)
    best_s = None
    rows = []
    for e1 in EXPIRY_EXH_TO_BUY:
        p = dict(best, expiry_exh_to_buy=float(e1))
        f5 = _eval_f5(p)
        s = _score(f5)
        rows.append({"e1": e1, "f5_n": f5.get("n"), "f5_pf": f5.get("pf")})
        if best_s is None or s > best_s:
            best_s, best = s, p
    best_s = None
    for e2 in EXPIRY_BUY_TO_RECLAIM:
        p = dict(best, expiry_buy_to_reclaim=float(e2))
        f5 = _eval_f5(p)
        s = _score(f5)
        rows.append({"e2": e2, "f5_n": f5.get("n"), "f5_pf": f5.get("pf")})
        if best_s is None or s > best_s:
            best_s, best = s, p
    diag["expiry"] = rows

    # 6 spread — only adopt if sample does not collapse vs unconstrained
    spreads = []
    for key, ticks in fit_s.items():
        for t in ticks[::40]:
            if t.board.canonical_spread_bps is not None:
                spreads.append(float(t.board.canonical_spread_bps))
    spreads.sort()
    rows = []
    base_n = (_eval_f5(best).get("n") or 0)
    if spreads and base_n > 0:
        best_s = _score(_eval_f5(best))
        kept = dict(best)
        for q in SPREAD_PERCENTILES:
            thr = spreads[int((len(spreads) - 1) * q)]
            p = dict(best, spread_max_bps=thr)
            f5 = _eval_f5(p)
            s = _score(f5)
            rows.append({"p": q, "thr": thr, "f5_n": f5.get("n"), "f5_pf": f5.get("pf")})
            if (f5.get("n") or 0) >= max(3, int(base_n * 0.5)) and s > best_s:
                best_s, kept = s, p
        best = kept
    diag["spread"] = rows
    best["diagnostics"] = diag
    return best


def train_gate(f5: dict, f0: dict, f2: dict, f3: dict) -> tuple[bool, str]:
    if (f5.get("n") or 0) < 30:
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:n<30"
    if (f5.get("pnl") or 0) <= 0 or (f5.get("mean") or 0) <= 0:
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:pnl"
    pf = f5.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1):
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:pf"
    if (f5.get("winner_rate") or 0) <= 0:
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:winner"
    if f0.get("never_rate") is not None and f5.get("never_rate") is not None and f5["never_rate"] >= f0["never_rate"]:
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:never"
    if f0.get("early_adverse_rate") is not None and f5.get("early_adverse_rate") is not None and f5["early_adverse_rate"] >= f0["early_adverse_rate"]:
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:early"
    if f2.get("stop_rate") is not None and f5.get("stop_rate") is not None and f5["stop_rate"] > f2["stop_rate"]:
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:stop"
    if (f5.get("top1_symbol_share") or 0) >= 0.40:
        return False, "NO_TRAIN_CANONICAL_FCR_CANDIDATE:symbol"
    return True, "TRAIN_PASS"


def val_gate(f5: dict) -> tuple[bool, str]:
    if (f5.get("n") or 0) < 5:
        return False, "NO_VALIDATED_CANONICAL_FCR_CANDIDATE:n"
    if (f5.get("pnl") or 0) <= 0 or (f5.get("mean") or 0) <= 0:
        return False, "NO_VALIDATED_CANONICAL_FCR_CANDIDATE:pnl"
    pf = f5.get("pf")
    if pf is None or (isinstance(pf, float) and pf <= 1):
        return False, "NO_VALIDATED_CANONICAL_FCR_CANDIDATE:pf"
    return True, "VALIDATION_PASS"
