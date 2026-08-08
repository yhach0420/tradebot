"""Lookback setup classification and evidence features (asof <= anchor_t)."""
from __future__ import annotations

from typing import Any, Optional

from .anchor import SymHist, _finite, _tick


def _slice(hist: SymHist, t0: float, t1: float) -> list[tuple[float, float]]:
    return [(tt, m) for (tt, m) in hist.mids if t0 - 1e-12 <= tt <= t1 + 1e-12]


def classify_setup(hist: SymHist, anchor: dict[str, Any], feats: dict[str, Any]) -> dict[str, Any]:
    """Mutually exclusive setup label using only pre-anchor history."""
    t = float(anchor["t"])
    atr = feats.get("atr_180s")
    if atr is None or atr <= 0:
        atr = hist.atr_proxy()
    if atr is None or atr <= 0:
        return {"setup_type": "NO_VALID_SETUP", "reason": "ATR_MISSING", "features": {}}

    # Prefer PULLBACK_RECLAIM when micro-high anchor or clear pullback geometry exists
    pb_low = anchor.get("pullback_low")
    local_high = anchor.get("local_high")
    pb_low_t = anchor.get("pullback_low_t")
    pullback_ok = False
    pb_feats: dict[str, Any] = {}
    if pb_low is not None and local_high is not None and pb_low_t is not None:
        depth = (float(local_high) - float(pb_low)) / atr
        dur = t - float(pb_low_t)
        sec_since_low = t - float(pb_low_t)
        pb_feats = {
            "local_high": local_high,
            "pullback_low": pb_low,
            "pullback_depth_atr": depth,
            "pullback_duration_sec": dur,
            "seconds_since_pullback_low": sec_since_low,
            "vwap_distance_atr": None,
        }
        if feats.get("vwap") is not None and feats.get("mid") is not None:
            pb_feats["vwap_distance_atr"] = (float(feats["mid"]) - float(feats["vwap"])) / atr
        # valid pullback: depth in reasonable band, real decline, low formed before cross
        if 0.10 <= depth <= 2.5 and dur >= 5.0 and float(pb_low) < float(local_high) - 1e-12:
            pullback_ok = True

    if anchor.get("anchor_kind") == "MICRO_HIGH" and pullback_ok:
        return {"setup_type": "PULLBACK_RECLAIM", "reason": "OK", "features": pb_feats}
    if pullback_ok and float(pb_feats.get("pullback_depth_atr") or 0) >= 0.15:
        return {"setup_type": "PULLBACK_RECLAIM", "reason": "OK", "features": pb_feats}

    # RANGE_BREAKOUT: range lookback exists with width and compression
    lb = float(anchor.get("range_lookback_sec") or 120.0)
    window = _slice(hist, t - lb, t - 1e-6)
    if len(window) >= 8:
        highs = [m for _, m in window]
        rh, rl = max(highs), min(highs)
        width = (rh - rl) / atr
        # high tests: approaches within 0.15 ATR of range high
        tests = sum(1 for m in highs if (rh - m) / atr <= 0.15)
        rng_feats = {
            "range_duration_sec": lb,
            "range_width_atr": width,
            "high_test_count": tests,
            "price_compression": width <= 1.5,
            "spread_bps": feats.get("spread_bps"),
        }
        if 0.15 <= width <= 3.0 and tests >= 2:
            return {"setup_type": "RANGE_BREAKOUT", "reason": "OK", "features": rng_feats}

    if pullback_ok:
        return {"setup_type": "PULLBACK_RECLAIM", "reason": "OK_FALLBACK", "features": pb_feats}
    return {"setup_type": "NO_VALID_SETUP", "reason": "NO_GEOMETRY", "features": pb_feats or {}}


def exhaustion_evidence(hist: SymHist, anchor: dict[str, Any], feats: dict[str, Any]) -> dict[str, Any]:
    """E1–E6; all asof <= anchor time (feats/hist already causal)."""
    t = float(anchor["t"])
    out = {f"E{i}": False for i in range(1, 7)}
    details: dict[str, Any] = {"asof_time": t}

    pb_low_t = anchor.get("pullback_low_t")
    # E1: no-new-low — pullback low not updated in last 15s before anchor
    if pb_low_t is not None and t - float(pb_low_t) >= 15.0:
        out["E1"] = True
    details["seconds_since_pullback_low"] = (
        None if pb_low_t is None else t - float(pb_low_t)
    )

    # E2: down-slope improvement — ret_15 >= ret_30
    r15, r30 = feats.get("ret_15s"), feats.get("ret_30s")
    if r15 is not None and r30 is not None and float(r15) >= float(r30) - 1e-12:
        out["E2"] = True
    details["ret_15s"] = r15
    details["ret_30s"] = r30

    # E3: downtick volume deceleration
    d15, d60 = feats.get("down_tick_volume_ratio_15s"), feats.get("down_tick_volume_ratio_60s")
    if d15 is not None and d60 is not None and float(d15) < float(d60) - 1e-12:
        out["E3"] = True
    details["down_tick_15"] = d15
    details["down_tick_60"] = d60

    # E4: bid decline stopped — last 3 bids non-decreasing
    bids = list(hist.bids)[-4:]
    if len(bids) >= 3:
        bvals = [b for _, b in bids]
        if bvals[-1] >= bvals[-2] - 1e-12 and bvals[-2] >= bvals[-3] - 1e-12:
            out["E4"] = True

    # E5: spread stable
    sp = feats.get("spread_bps")
    if sp is not None and float(sp) <= 8.0:
        out["E5"] = True
    details["spread_bps"] = sp

    # E6: reclaim level recovered — mid above reference and above pullback mid-range
    ref = float(anchor["reference_high"])
    mid = float(anchor["mid"])
    pb = anchor.get("pullback_low")
    if mid > ref + 1e-12 and (pb is None or mid > (float(pb) + ref) / 2.0):
        out["E6"] = True

    passed = [k for k, v in out.items() if v]
    return {"flags": out, "passed": passed, "n_passed": len(passed), "details": details}


def dynamic_evidence(feats: dict[str, Any], anchor: dict[str, Any]) -> dict[str, Any]:
    flags = {
        "volume_impulse": False,
        "uptick_improvement": False,
        "price_update_acceleration": False,
        "bid_support": False,
    }
    med10 = feats.get("median_active_volume_10s_120s")
    med30 = feats.get("median_active_volume_30s_300s")
    vol10, vol30 = feats.get("volume_10s"), feats.get("volume_30s")
    if (
        med10 and med10 > 0 and med30 and med30 > 0
        and vol10 is not None and vol30 is not None
        and float(vol10) / float(med10) >= 1.25
        and float(vol30) / float(med30) >= 1.10
    ):
        flags["volume_impulse"] = True

    ur = feats.get("uptick_volume_ratio_30s")
    if ur is not None and float(ur) >= 0.55:
        flags["uptick_improvement"] = True

    pu10 = feats.get("price_update_count_10s")
    pu_med = feats.get("median_price_update_count_10s_120s")
    if pu10 is not None and pu_med is not None and float(pu10) > float(pu_med):
        flags["price_update_acceleration"] = True

    ref = float(anchor["reference_high"])
    tick = float(anchor.get("tick") or _tick(ref))
    bid = float(anchor["bid"])
    if bid >= ref - tick - 1e-12:
        flags["bid_support"] = True

    passed = [k for k, v in flags.items() if v]
    return {"flags": flags, "passed": passed, "n_passed": len(passed)}


def profile_pass(profile: str, setup_ok: bool, exh: dict, dyn: dict, feats: dict, anchor: dict) -> bool:
    if not setup_ok:
        return False
    if profile == "TAER_P0":
        return True
    if profile == "TAER_P1":
        return exh["n_passed"] >= 1 and dyn["n_passed"] >= 1
    if profile == "TAER_P2":
        return exh["n_passed"] >= 2 and dyn["n_passed"] >= 2
    if profile == "TAER_P3":
        # volume impulse + uptick + spread not widening
        sp = feats.get("spread_bps")
        spread_ok = sp is not None and float(sp) <= 8.0
        return bool(
            dyn["flags"].get("volume_impulse")
            and dyn["flags"].get("uptick_improvement")
            and spread_ok
        )
    return False
