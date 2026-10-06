"""CLEAN_OPENING_DRIVE_V3. Dominant OR15 auction. 09:00–09:14 only. No future."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.impulse import opening_path
from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v3_1_face_validity_fix.drive import opening_impulse_lost
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import (
    OR_CLOSE_HALF,
    TWO_SIDED_EXCURSION,
    TWO_SIDED_LOC_HI,
    TWO_SIDED_LOC_LO,
)

ALLOWED_AUCTIONS = ("INITIAL_DIRECTION_CONTINUATION", "EARLY_REVERSAL_THEN_DOMINANT_DRIVE")


def _ratio(num: Any, den: Any) -> float | None:
    if not (_finite(num) and _finite(den) and float(den) > 0):
        return None
    return float(num) / float(den)


def directional_efficiency(closes: list[float], anchor: Any, *, sign: int) -> float | None:
    if not closes or not _finite(anchor):
        return None
    net = (float(closes[-1]) - float(anchor)) * (1 if int(sign) >= 0 else -1)
    path = 0.0
    prev = float(anchor)
    for c in closes:
        if not _finite(c):
            continue
        path += abs(float(c) - prev)
        prev = float(c)
    if path <= 0:
        return None
    return float(net / path)


def extract_or_bars(rec: dict[str, Any], session_idx: list[int]) -> list[tuple[str, float, float, float, float]]:
    bars: list[tuple[str, float, float, float, float]] = []
    for i in session_idx:
        t = str(rec["t"][i])
        if t < "09:00" or t > "09:14":
            continue
        o, h, l, c = rec["o"][i], rec["h"][i], rec["l"][i], rec["c"][i]
        if not (_finite(o) and _finite(h) and _finite(l) and _finite(c)):
            continue
        bars.append((t, float(o), float(h), float(l), float(c)))
    return bars


def drive_features(path: dict[str, Any], bars: list[tuple[str, float, float, float, float]] | None = None) -> dict[str, Any]:
    """Causal 09:00–09:14 descriptors. No post-09:14. No gating here."""
    bars = list(bars or [])
    rng = path.get("or_range")
    or_h, or_l = path.get("or_high"), path.get("or_low")
    c15, opn = path.get("close_0914"), path.get("open_0900")
    loc = None
    if _finite(or_h) and _finite(or_l) and float(or_h) > float(or_l) and _finite(c15):
        loc = (float(c15) - float(or_l)) / (float(or_h) - float(or_l))
    first5 = [b for b in bars if b[0] <= "09:04"]
    last5 = [b for b in bars if b[0] >= "09:10"]
    f5_hi = max((b[2] for b in first5), default=None)
    f5_lo = min((b[3] for b in first5), default=None)
    closes = [b[4] for b in bars]
    last5_closes = [b[4] for b in last5]
    n_up = n_dn = 0
    prev = bars[0][1] if bars else None
    largest_up = largest_dn = 0.0
    run_up = run_dn = 0.0
    if prev is not None:
        for b in bars:
            ch = b[4] - prev
            if ch > 0:
                n_up += 1
                run_up += ch
                run_dn = 0.0
                largest_up = max(largest_up, run_up)
            elif ch < 0:
                n_dn += 1
                run_dn += -ch
                run_up = 0.0
                largest_dn = max(largest_dn, run_dn)
            prev = b[4]
    last5_net = (last5_closes[-1] - last5_closes[0]) if len(last5_closes) >= 2 else None
    return {
        "or_close_loc": loc,
        "dist_from_or_low_over_or": _ratio((float(c15) - float(or_l)) if _finite(c15) and _finite(or_l) else None, rng),
        "dist_from_or_high_over_or": _ratio((float(or_h) - float(c15)) if _finite(c15) and _finite(or_h) else None, rng),
        "net_session_open_over_or": _ratio(path.get("net_or15"), rng),
        "net_first5_over_or": _ratio(path.get("net_5m"), rng),
        "mfe_over_or": _ratio(path.get("mfe_up"), rng),
        "mae_over_or": _ratio(path.get("mae_dn"), rng),
        "eff_session_open": directional_efficiency(closes, opn, sign=1),
        "eff_first5_low": directional_efficiency(closes, f5_lo, sign=1) if f5_lo is not None else None,
        "eff_first5_high": directional_efficiency(closes, f5_hi, sign=-1) if f5_hi is not None else None,
        "eff_or_low": directional_efficiency(closes, or_l, sign=1),
        "eff_or_high": directional_efficiency(closes, or_h, sign=-1),
        "n_up_closes": n_up,
        "n_dn_closes": n_dn,
        "largest_up_run_over_or": _ratio(largest_up, rng),
        "largest_dn_run_over_or": _ratio(largest_dn, rng),
        "last5_net": last5_net,
        "last5_eff_up": directional_efficiency(last5_closes, last5_closes[0], sign=1) if len(last5_closes) >= 2 else None,
        "first_or_high_t": path.get("first_or_high_t"),
        "first_or_low_t": path.get("first_or_low_t"),
        "dist_from_session_open": (float(c15) - float(opn)) if _finite(c15) and _finite(opn) else None,
        "first5_high": f5_hi,
        "first5_low": f5_lo,
        "or_close_half": float(OR_CLOSE_HALF),
        "efficiency_gated": False,
    }


def classify_auction(feat: dict[str, Any]) -> str:
    loc = feat.get("or_close_loc")
    mfe = feat.get("mfe_over_or")
    mae = feat.get("mae_over_or")
    n5 = feat.get("net_first5_over_or")
    if not _finite(loc):
        return "RANGE_OPEN"
    loc = float(loc)
    midish = float(TWO_SIDED_LOC_LO) < loc < float(TWO_SIDED_LOC_HI)
    two = (
        midish
        and _finite(mfe)
        and _finite(mae)
        and float(mfe) >= float(TWO_SIDED_EXCURSION)
        and float(mae) >= float(TWO_SIDED_EXCURSION)
    )
    if two:
        return "TWO_SIDED_OPEN"
    if midish:
        return "RANGE_OPEN"
    if loc >= float(OR_CLOSE_HALF):
        if _finite(n5) and float(n5) < 0:
            return "EARLY_REVERSAL_THEN_DOMINANT_DRIVE"
        return "INITIAL_DIRECTION_CONTINUATION"
    if _finite(n5) and float(n5) > 0:
        return "EARLY_REVERSAL_THEN_DOMINANT_DRIVE"
    return "INITIAL_DIRECTION_CONTINUATION"


def classify_drive(path: dict[str, Any], bars: list[tuple[str, float, float, float, float]] | None = None) -> dict[str, Any]:
    """CLEAN_OPENING_DRIVE_V3 = dominant OR15 auction. Not session-open monotonic."""
    if not path.get("ok"):
        return {"state": "NON_DIRECTIONAL_OPEN", "DIR": 0, "reason": "or_incomplete", "auction": "RANGE_OPEN"}
    rng = path.get("or_range")
    if not _finite(rng) or float(rng) <= 0:
        return {"state": "NON_DIRECTIONAL_OPEN", "DIR": 0, "reason": "or_range_zero", "auction": "RANGE_OPEN"}
    feat = drive_features(path, bars)
    auction = classify_auction(feat)
    loc = feat.get("or_close_loc")
    if auction not in ALLOWED_AUCTIONS or not _finite(loc):
        return {
            "state": "NON_DIRECTIONAL_OPEN",
            "DIR": 0,
            "reason": f"auction_{auction}",
            "auction": auction,
            **feat,
            "session_open_net_required": False,
            "first5_same_sign_required": False,
            "drive_frac_searched": False,
        }
    direction = 1 if float(loc) >= float(OR_CLOSE_HALF) else -1
    n_dir = int(feat.get("n_up_closes") or 0) if direction > 0 else int(feat.get("n_dn_closes") or 0)
    n_opp = int(feat.get("n_dn_closes") or 0) if direction > 0 else int(feat.get("n_up_closes") or 0)
    return {
        "state": "CLEAN_OPENING_DRIVE_V3",
        "DIR": direction,
        "reason": "dominant_or15_auction",
        "auction": auction,
        "n_dir_closes": n_dir,
        "n_opp_closes": n_opp,
        "session_open_net_required": False,
        "first5_same_sign_required": False,
        "drive_frac_searched": False,
        **feat,
    }


def signed_efficiency(feat: dict[str, Any], *, sign: int) -> float | None:
    if int(sign) > 0:
        return feat.get("eff_or_low")
    return feat.get("eff_or_high")


_ = opening_path
_ = opening_impulse_lost
