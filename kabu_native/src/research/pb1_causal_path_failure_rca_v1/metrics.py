"""Stage paths, consumption, first-passage, OR-fail class, RISK_INVALID class. No retune."""
from __future__ import annotations

from typing import Any

from research.pb1_causal_path_failure_rca_v1 import FAIL_WINDOWS, HORIZONS, MFE_R_GATES, SESSION_FLAT
from research.pb1_opening_range_causal_path_test_v1.path import (
    _fav_adv,
    _finite,
    _or_accept_fail,
    _retest_breach,
    signed_path,
)


def _loc(session_idx: list[int], pos: int) -> int | None:
    for k, i in enumerate(session_idx):
        if i == pos:
            return k
    return None


def stage_path(rec: dict[str, Any], *, pos: int, sign: int, or_high: float, or_low: float, px0: Any) -> dict[str, Any]:
    """Direction-normalized path from a completed-bar close (or given px0)."""
    p = signed_path(
        rec,
        session_idx=rec["session_idx"],
        entry_pos=int(pos),
        sign=int(sign),
        or_high=float(or_high),
        or_low=float(or_low),
        retest_high=None,
        retest_low=None,
        target_px=None,
        px0_override=float(px0) if _finite(px0) else None,
    )
    out = {f"r{h}_bps": p.get(f"r{h}_bps") for h in HORIZONS}
    out["MFE_bps"] = p.get("MFE_bps")
    out["MAE_bps"] = p.get("MAE_bps")
    out["time_to_MFE"] = p.get("time_to_MFE")
    out["time_to_MAE"] = p.get("time_to_MAE")
    return out


def signed_move(sign: int, a: Any, b: Any) -> float:
    if not (_finite(a) and _finite(b) and float(a) > 0):
        return float("nan")
    return float(sign) * (float(b) / float(a) - 1.0) * 10000.0


def units(move_bps: Any, *, px: Any, or_range: Any, atr: Any, r_px: Any) -> dict[str, Any]:
    if not _finite(move_bps) or not _finite(px) or float(px) <= 0:
        return {"bps": float("nan"), "or_units": float("nan"), "atr_units": float("nan"), "R_units": float("nan")}
    px_move = float(move_bps) / 10000.0 * float(px)
    return {
        "bps": float(move_bps),
        "or_units": float(px_move / float(or_range)) if _finite(or_range) and float(or_range) > 0 else float("nan"),
        "atr_units": float(px_move / float(atr)) if _finite(atr) and float(atr) > 0 else float("nan"),
        "R_units": float(px_move / float(r_px)) if _finite(r_px) and float(r_px) > 0 else float("nan"),
    }


def break_window_stats(
    rec: dict[str, Any],
    *,
    sign: int,
    break_pos: int,
    retest_pos: Any,
    entry_pos: Any,
    trigger_pos: Any,
    or_range: Any,
    atr: Any,
    r_px: Any,
) -> dict[str, Any]:
    session_idx = rec["session_idx"]
    loc = _loc(session_idx, break_pos)
    out: dict[str, Any] = {
        "break_to_entry": units(float("nan"), px=None, or_range=or_range, atr=atr, r_px=r_px),
        "max_ext_before_retest": units(float("nan"), px=None, or_range=or_range, atr=atr, r_px=r_px),
        "ext_to_retest_retrace": units(float("nan"), px=None, or_range=or_range, atr=atr, r_px=r_px),
        "retest_to_trigger": units(float("nan"), px=None, or_range=or_range, atr=atr, r_px=r_px),
        "trigger_to_entry": units(float("nan"), px=None, or_range=or_range, atr=atr, r_px=r_px),
    }
    if loc is None:
        return out
    brk_c = rec["c"][break_pos]
    if not _finite(brk_c) or float(brk_c) <= 0:
        return out
    sign = int(sign)
    if _finite(entry_pos):
        entry = rec["o"][int(entry_pos)]
        out["break_to_entry"] = units(signed_move(sign, brk_c, entry), px=brk_c, or_range=or_range, atr=atr, r_px=r_px)
    mfe = None
    mfe_px = None
    end = _loc(session_idx, int(retest_pos)) if _finite(retest_pos) else None
    last = end if end is not None else loc
    for k in range(loc, last + 1):
        i = session_idx[k]
        h, l = rec["h"][i], rec["l"][i]
        if not (_finite(h) and _finite(l)):
            continue
        fav, _adv = _fav_adv(float(brk_c), float(h), float(l), sign)
        if mfe is None or fav > mfe:
            mfe = fav
            mfe_px = float(h) if sign > 0 else float(l)
    if mfe is not None:
        out["max_ext_before_retest"] = units(mfe, px=brk_c, or_range=or_range, atr=atr, r_px=r_px)
        if _finite(retest_pos) and _finite(mfe_px):
            ret_c = rec["c"][int(retest_pos)]
            retr = signed_move(sign, mfe_px, ret_c)
            out["ext_to_retest_retrace"] = units(retr, px=mfe_px, or_range=or_range, atr=atr, r_px=r_px)
    if _finite(retest_pos) and _finite(trigger_pos):
        out["retest_to_trigger"] = units(
            signed_move(sign, rec["c"][int(retest_pos)], rec["c"][int(trigger_pos)]),
            px=rec["c"][int(retest_pos)],
            or_range=or_range,
            atr=atr,
            r_px=r_px,
        )
    if _finite(trigger_pos) and _finite(entry_pos):
        out["trigger_to_entry"] = units(
            signed_move(sign, rec["c"][int(trigger_pos)], rec["o"][int(entry_pos)]),
            px=rec["c"][int(trigger_pos)],
            or_range=or_range,
            atr=atr,
            r_px=r_px,
        )
    return out


def first_passage(
    rec: dict[str, Any],
    *,
    entry_pos: int,
    sign: int,
    entry: float,
    r_px: Any,
    or_high: float,
    or_low: float,
    retest_high: Any,
    retest_low: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "fail_t": float("nan"),
        "or_t": float("nan"),
        "ret_t": float("nan"),
        "adv_0_5R_t": float("nan"),
        "adv_1_0R_t": float("nan"),
    }
    for g in MFE_R_GATES:
        key = str(g).replace(".", "_")
        out[f"plus_{key}R_t"] = float("nan")
        out[f"plus_{key}R_before_fail"] = False
    if not (_finite(r_px) and float(r_px) > 0 and _finite(entry) and float(entry) > 0):
        out["risk_defined"] = False
        return out
    out["risk_defined"] = True
    r_bps = float(r_px) / float(entry) * 10000.0
    loc = _loc(rec["session_idx"], entry_pos)
    if loc is None:
        return out
    or_t = ret_t = fail_t = None
    hit: dict[float, float] = {}
    adv_hit: dict[float, float] = {}
    for step, k in enumerate(range(loc, len(rec["session_idx"]))):
        i = rec["session_idx"][k]
        t = str(rec["t"][i])
        if t >= SESSION_FLAT:
            break
        h, l, c = rec["h"][i], rec["l"][i], rec["c"][i]
        if not (_finite(h) and _finite(l)):
            continue
        fav, adv = _fav_adv(float(entry), float(h), float(l), int(sign))
        mins = float(step + 1)
        for g in MFE_R_GATES:
            if g not in hit and fav >= g * r_bps:
                hit[g] = mins
        for g in (0.5, 1.0):
            if g not in adv_hit and adv <= -g * r_bps:
                adv_hit[g] = mins
        if or_t is None and _finite(c) and _or_accept_fail(float(c), or_high, or_low, int(sign)):
            or_t = mins
        if ret_t is None and _retest_breach(float(h), float(l), retest_high, retest_low, int(sign)):
            ret_t = mins
        if fail_t is None:
            cands = [x for x in (or_t, ret_t) if x is not None]
            if cands:
                fail_t = min(cands)
    out["or_t"] = or_t if or_t is not None else float("nan")
    out["ret_t"] = ret_t if ret_t is not None else float("nan")
    out["fail_t"] = fail_t if fail_t is not None else float("nan")
    out["adv_0_5R_t"] = adv_hit.get(0.5, float("nan"))
    out["adv_1_0R_t"] = adv_hit.get(1.0, float("nan"))
    for g in MFE_R_GATES:
        key = str(g).replace(".", "_")
        t0 = hit.get(g)
        out[f"plus_{key}R_t"] = t0 if t0 is not None else float("nan")
        out[f"plus_{key}R_before_fail"] = bool(t0 is not None and (fail_t is None or t0 <= fail_t))
    for w in FAIL_WINDOWS:
        out[f"or_fail_within_{w}m"] = bool(or_t is not None and or_t <= w)
        out[f"ret_fail_within_{w}m"] = bool(ret_t is not None and ret_t <= w)
    return out


def or_fail_class(
    rec: dict[str, Any],
    *,
    entry_pos: int,
    sign: int,
    or_high: float,
    or_low: float,
    entry: float,
) -> dict[str, Any]:
    loc = _loc(rec["session_idx"], entry_pos)
    empty = {
        "or_failed": False,
        "class": None,
        "depth_or_units": float("nan"),
        "bars_inside": 0,
        "time_to_reclaim": float("nan"),
        "mfe_after_fail_bps": float("nan"),
        "reclaim_after_failure": False,
        "shallow_one_bar": False,
        "persistent_inside": False,
        "deep_failure": False,
    }
    if loc is None or not _finite(entry) or float(entry) <= 0:
        return empty
    or_range = float(or_high) - float(or_low)
    fail_k = None
    depth = 0.0
    inside_n = 0
    reclaim_t = None
    mfe_after = None
    for step, k in enumerate(range(loc, len(rec["session_idx"]))):
        i = rec["session_idx"][k]
        t = str(rec["t"][i])
        if t >= SESSION_FLAT:
            break
        h, l, c = rec["h"][i], rec["l"][i], rec["c"][i]
        mins = float(step + 1)
        if fail_k is None:
            if _finite(c) and _or_accept_fail(float(c), or_high, or_low, int(sign)):
                fail_k = k
            else:
                continue
        if not (_finite(h) and _finite(l)):
            continue
        if sign > 0:
            d = (float(or_high) - float(l)) / or_range if or_range > 0 else 0.0
            inside = _finite(c) and float(c) < float(or_high)
            outside = _finite(c) and float(c) >= float(or_high)
        else:
            d = (float(h) - float(or_low)) / or_range if or_range > 0 else 0.0
            inside = _finite(c) and float(c) > float(or_low)
            outside = _finite(c) and float(c) <= float(or_low)
        depth = max(depth, d)
        if inside:
            inside_n += 1
        fav, _adv = _fav_adv(float(entry), float(h), float(l), int(sign))
        if mfe_after is None or fav > mfe_after:
            mfe_after = fav
        if reclaim_t is None and outside and k != fail_k:
            reclaim_t = mins
            break
    if fail_k is None:
        return empty
    shallow = inside_n <= 1 and depth < 0.25
    persistent = inside_n >= 3
    deep = depth >= 0.50
    reclaim = reclaim_t is not None
    if deep:
        klass = "DEEP_FAILURE"
    elif persistent:
        klass = "PERSISTENT_ACCEPTANCE_INSIDE"
    elif shallow:
        klass = "SHALLOW_ONE_BAR_REENTRY"
    elif reclaim:
        klass = "RECLAIM_AFTER_FAILURE"
    else:
        klass = "PERSISTENT_ACCEPTANCE_INSIDE" if inside_n >= 2 else "SHALLOW_ONE_BAR_REENTRY"
    return {
        "or_failed": True,
        "class": klass,
        "depth_or_units": float(depth),
        "bars_inside": int(inside_n),
        "time_to_reclaim": reclaim_t if reclaim_t is not None else float("nan"),
        "mfe_after_fail_bps": mfe_after if mfe_after is not None else float("nan"),
        "reclaim_after_failure": bool(reclaim),
        "shallow_one_bar": bool(shallow),
        "persistent_inside": bool(persistent),
        "deep_failure": bool(deep),
    }


def risk_invalid_why(sign: int, entry: Any, retest_high: Any, retest_low: Any, trigger_high: Any, trigger_low: Any, labels: list[str]) -> str:
    if sign > 0:
        ext = retest_low
        trig_ext = trigger_low
        beyond = _finite(entry) and _finite(ext) and float(entry) <= float(ext)
        new_ext = _finite(trig_ext) and _finite(ext) and float(trig_ext) < float(ext)
    else:
        ext = retest_high
        trig_ext = trigger_high
        beyond = _finite(entry) and _finite(ext) and float(entry) >= float(ext)
        new_ext = _finite(trig_ext) and _finite(ext) and float(trig_ext) > float(ext)
    if not _finite(ext):
        return "retest_extreme_not_representable"
    if beyond:
        return "entry_already_beyond_retest_extreme"
    if new_ext:
        return "failed_push_created_a_new_extreme"
    if len(labels) >= 2:
        return "same_bar_geometry_ambiguity"
    return "other"


def opening_shape(path: dict[str, Any], rec: dict[str, Any], session_idx: list[int], sign: int) -> dict[str, Any]:
    abs_sum = 0.0
    n = 0
    prev = None
    for i in session_idx:
        t = str(rec["t"][i])
        if t < "09:00" or t > "09:14":
            continue
        c = rec["c"][i]
        if not _finite(c):
            continue
        if prev is not None:
            abs_sum += abs(float(c) - float(prev))
            n += 1
        prev = float(c)
    net = path.get("net_or15")
    eff = abs(float(net)) / abs_sum if _finite(net) and abs_sum > 0 else float("nan")
    rng = path.get("or_range")
    mfe = path.get("mfe_up")
    mae = path.get("mae_dn")
    if sign > 0:
        counter = float(mae) / float(rng) if _finite(mae) and _finite(rng) and float(rng) > 0 else float("nan")
        asym = float(mfe) / float(mae) if _finite(mfe) and _finite(mae) and float(mae) > 0 else float("nan")
    else:
        counter = float(mfe) / float(rng) if _finite(mfe) and _finite(rng) and float(rng) > 0 else float("nan")
        asym = float(mae) / float(mfe) if _finite(mae) and _finite(mfe) and float(mfe) > 0 else float("nan")
    return {
        "opening_efficiency": eff,
        "counter_move_or_units": counter,
        "first_or_high_t": path.get("first_or_high_t"),
        "first_or_low_t": path.get("first_or_low_t"),
        "opening_mfe_mae_asym": asym,
        "or_abs_path_n": n,
    }
