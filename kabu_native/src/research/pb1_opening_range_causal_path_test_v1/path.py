"""Signed next-open path, structural failure, and outcome-blind target metadata."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_causal_path_test_v1 import HORIZONS, PATH_WINDOW, SESSION_FLAT
from research.pb1_opening_range_continuation_face_valid_v2.machine import vwap_location
from research.pb1_opening_range_continuation_face_valid_v2.walk import _next_open


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def trigger_to_entry_bps(trigger_close: Any, entry: Any) -> float:
    if not (_finite(trigger_close) and _finite(entry) and float(trigger_close) > 0):
        return float("nan")
    return float((float(entry) / float(trigger_close) - 1.0) * 10000.0)


def structural_r(*, sign: int, entry: float, retest_high: Any, retest_low: Any) -> dict[str, Any]:
    if not _finite(entry) or entry <= 0:
        return {"R": float("nan"), "risk_state": "RISK_UNDEFINED"}
    if sign > 0:
        inv = float(retest_low) if _finite(retest_low) else float("nan")
        r = (entry - inv) if _finite(inv) else float("nan")
    else:
        inv = float(retest_high) if _finite(retest_high) else float("nan")
        r = (inv - entry) if _finite(inv) else float("nan")
    if not _finite(r) or r <= 0:
        return {"R": r if _finite(r) else float("nan"), "risk_state": "RISK_INVALID"}
    return {"R": float(r), "risk_state": "RISK_DEFINED", "R_bps": float(r / entry * 10000.0)}


def target_semantics(*, sign: int, entry: float, levels: dict[str, Any], r_px: Any) -> dict[str, Any]:
    ahead: list[tuple[str, float]] = []
    for name, lvl in levels.items():
        if not _finite(lvl):
            continue
        if sign > 0 and float(lvl) > entry:
            ahead.append((name, float(lvl)))
        if sign < 0 and float(lvl) < entry:
            ahead.append((name, float(lvl)))
    if sign > 0:
        ahead.sort(key=lambda x: x[1])
    else:
        ahead.sort(key=lambda x: -x[1])
    nearest = ahead[0] if ahead else None
    dist = abs(nearest[1] - entry) if nearest else float("nan")
    dist_bps = float(dist / entry * 10000.0) if nearest and entry else float("nan")
    if not _finite(r_px) or float(r_px) <= 0:
        ratio = float("nan")
        risk_state = "RISK_UNDEFINED"
    else:
        ratio = float(dist / float(r_px)) if nearest else float("nan")
        risk_state = "RISK_DEFINED"
    if nearest is None:
        kind = "NO_PREKNOWN_TARGET_AHEAD"
        note = "open_air_or_target_unavailable_under_current_historical_references"
    else:
        kind = "TARGET_AHEAD"
        note = None
    return {
        "target_kind": kind,
        "target_note": note,
        "nearest_target": None if nearest is None else nearest[0],
        "nearest_target_px": None if nearest is None else nearest[1],
        "target_distance": dist,
        "target_distance_bps": dist_bps,
        "target_distance_R": ratio,
        "target_names_ahead": [n for n, _ in ahead],
        "risk_state_for_target": risk_state,
    }


def _fav_adv(entry: float, high: float, low: float, sign: int) -> tuple[float, float]:
    up = (float(high) / entry - 1.0) * 10000.0
    dn = (float(low) / entry - 1.0) * 10000.0
    a, b = float(sign) * up, float(sign) * dn
    return max(a, b), min(a, b)


def _or_accept_fail(close: float, or_high: float, or_low: float, sign: int) -> bool:
    if sign > 0:
        return bool(close < float(or_high))
    return bool(close > float(or_low))


def _retest_breach(high: float, low: float, retest_high: Any, retest_low: Any, sign: int) -> bool:
    if sign > 0:
        return bool(_finite(retest_low) and float(low) < float(retest_low))
    return bool(_finite(retest_high) and float(high) > float(retest_high))


def _target_touch(high: float, low: float, target: Any, sign: int) -> bool:
    if not _finite(target):
        return False
    if sign > 0:
        return bool(float(high) >= float(target))
    return bool(float(low) <= float(target))


def signed_path(
    rec: dict[str, Any],
    *,
    session_idx: list[int],
    entry_pos: int,
    sign: int,
    or_high: float,
    or_low: float,
    retest_high: Any,
    retest_low: Any,
    target_px: Any,
    px0_override: float | None = None,
) -> dict[str, Any]:
    loc = None
    for k, i in enumerate(session_idx):
        if i == entry_pos:
            loc = k
            break
    out: dict[str, Any] = {
        "entry_t": None,
        "entry_px": None,
        "fwd_n": 0,
        "or_accept_fail": False,
        "retest_extreme_breach": False,
        "time_to_OR_failure": float("nan"),
        "time_to_retest_extreme_breach": float("nan"),
        "time_to_MFE": float("nan"),
        "time_to_MAE": float("nan"),
        "TARGET_HIT_BEFORE_OR_FAILURE": False,
        "TARGET_HIT_BEFORE_RETEST_EXTREME_BREACH": False,
        "time_to_target": float("nan"),
        "mfe_before_or_fail_bps": float("nan"),
    }
    for h in HORIZONS:
        out[f"r{h}_bps"] = float("nan")
        out[f"MFE_{h}m"] = float("nan")
        out[f"MAE_{h}m"] = float("nan")
    if loc is None:
        return out
    entry = float(px0_override) if _finite(px0_override) else float(rec["o"][entry_pos])
    if not _finite(entry) or entry <= 0:
        return out
    out["entry_t"] = str(rec["t"][entry_pos])
    out["entry_px"] = entry
    mfe = mae = None
    mfe_t = mae_t = None
    mfe_before_or = None
    or_t = ret_t = tgt_t = None
    or_hit = ret_hit = tgt_hit = False
    tgt_before_or = tgt_before_ret = False
    last_k = None
    max_h = max(HORIZONS)
    path_cap = max(PATH_WINDOW, max_h)
    for step, k in enumerate(range(loc, len(session_idx))):
        i = session_idx[k]
        t = str(rec["t"][i])
        if t >= SESSION_FLAT:
            break
        h, l, c = rec["h"][i], rec["l"][i], rec["c"][i]
        if not (_finite(h) and _finite(l)):
            continue
        fav, adv = _fav_adv(entry, float(h), float(l), sign)
        mins = step + 1
        if mins <= path_cap:
            if mfe is None or fav > mfe:
                mfe, mfe_t = fav, float(mins)
            if mae is None or adv < mae:
                mae, mae_t = adv, float(mins)
        if (not or_hit) and _finite(c) and _or_accept_fail(float(c), or_high, or_low, sign):
            or_hit = True
            or_t = float(mins)
        if not or_hit:
            if mfe_before_or is None or fav > mfe_before_or:
                mfe_before_or = fav
        if (not ret_hit) and _retest_breach(float(h), float(l), retest_high, retest_low, sign):
            ret_hit = True
            ret_t = float(mins)
        if (not tgt_hit) and _target_touch(float(h), float(l), target_px, sign):
            tgt_hit = True
            tgt_t = float(mins)
            if not or_hit:
                tgt_before_or = True
            if not ret_hit:
                tgt_before_ret = True
        if mins in HORIZONS:
            out[f"MFE_{mins}m"] = mfe if mfe is not None else float("nan")
            out[f"MAE_{mins}m"] = mae if mae is not None else float("nan")
            if _finite(c):
                out[f"r{mins}_bps"] = float(sign) * (float(c) / entry - 1.0) * 10000.0
        last_k = step
    out.update(
        {
            "fwd_n": int(last_k + 1) if last_k is not None else 0,
            "MFE_bps": mfe if mfe is not None else float("nan"),
            "MAE_bps": mae if mae is not None else float("nan"),
            "time_to_MFE": mfe_t if mfe_t is not None else float("nan"),
            "time_to_MAE": mae_t if mae_t is not None else float("nan"),
            "or_accept_fail": or_hit,
            "retest_extreme_breach": ret_hit,
            "time_to_OR_failure": or_t if or_t is not None else float("nan"),
            "time_to_retest_extreme_breach": ret_t if ret_t is not None else float("nan"),
            "TARGET_HIT_BEFORE_OR_FAILURE": tgt_before_or,
            "TARGET_HIT_BEFORE_RETEST_EXTREME_BREACH": tgt_before_ret,
            "time_to_target": tgt_t if tgt_t is not None else float("nan"),
            "mfe_before_or_fail_bps": mfe_before_or if mfe_before_or is not None else (mfe if mfe is not None else float("nan")),
        }
    )
    return out


def ratios_to_r(path: dict[str, Any], r_px: Any, entry: Any) -> dict[str, Any]:
    if not (_finite(r_px) and _finite(entry) and float(r_px) > 0 and float(entry) > 0):
        return {"MFE_over_R": float("nan"), "MAE_over_R": float("nan"), "mfe_before_or_fail_over_R": float("nan")}
    r_bps = float(r_px) / float(entry) * 10000.0
    if r_bps <= 0:
        return {"MFE_over_R": float("nan"), "MAE_over_R": float("nan"), "mfe_before_or_fail_over_R": float("nan")}

    def _div(x: Any) -> float:
        return float(x) / r_bps if _finite(x) else float("nan")

    return {
        "MFE_over_R": _div(path.get("MFE_bps")),
        "MAE_over_R": _div(path.get("MAE_bps")),
        "mfe_before_or_fail_over_R": _div(path.get("mfe_before_or_fail_bps")),
        "R_bps": r_bps,
    }


def levels_at(ev: dict[str, Any], rec: dict[str, Any], pos: int) -> dict[str, float]:
    return {
        "PDH": ev.get("pdh"),
        "PDL": ev.get("pdl"),
        "PDC": ev.get("pdc"),
        "D5H": ev.get("d5h"),
        "D5L": ev.get("d5l"),
        "SMA25": ev.get("sma25"),
        "SMA75": ev.get("sma75"),
        "VWAP": rec["vw"][pos] if pos < len(rec["vw"]) else float("nan"),
    }


def loc_features(px: Any, atr: Any, pdh: Any, pdl: Any, d5h: Any, d5l: Any) -> dict[str, Any]:
    def d(level: Any) -> float:
        if not (_finite(px) and _finite(level) and _finite(atr) and float(atr) > 0):
            return float("nan")
        return float((float(px) - float(level)) / float(atr))

    return {
        "dist_pdh_atr": d(pdh),
        "dist_pdl_atr": d(pdl),
        "dist_d5h_atr": d(d5h),
        "dist_d5l_atr": d(d5l),
        "side_pdh": "above" if _finite(px) and _finite(pdh) and float(px) >= float(pdh) else ("below" if _finite(px) and _finite(pdh) else "na"),
        "side_pdl": "above" if _finite(px) and _finite(pdl) and float(px) >= float(pdl) else ("below" if _finite(px) and _finite(pdl) else "na"),
    }


def vwap_at(rec: dict[str, Any], pos: int, sign: int) -> str:
    return vwap_location(rec["h"][pos], rec["l"][pos], rec["c"][pos], rec["vw"][pos], sign)


def next_open_from(rec: dict[str, Any], session_idx: list[int], pos: int) -> dict[str, Any] | None:
    return _next_open(rec, session_idx, pos)
