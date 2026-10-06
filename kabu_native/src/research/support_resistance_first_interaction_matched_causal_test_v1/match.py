"""Matched non-zone bars and causal placebo levels. Match on pre-event features only."""
from __future__ import annotations

from typing import Any

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import ZONE_HALF_ATR
from research.support_resistance_first_interaction_matched_causal_test_v1 import MOM1_TOL, MOM3_TOL, MOM5_TOL, RNG_REL_TOL, VOL_REL_TOL


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _close(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) <= float(tol)


def _rel(a: Any, b: Any, tol: float) -> bool:
    if not _finite(a) and not _finite(b):
        return True
    if not _finite(a) or not _finite(b):
        return False
    den = max(abs(float(b)), 1e-9)
    return abs(float(a) - float(b)) / den <= float(tol)


def _overlap_bands(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> bool:
    return max(lo_a, lo_b) <= min(hi_a, hi_b)


def placebo_zones(*, prior: dict[str, Any] | None, atr: float, active: list[dict[str, Any]], open_px: float) -> list[dict[str, Any]]:
    if not prior or not _finite(atr) or atr <= 0:
        return []
    hw = float(ZONE_HALF_ATR) * float(atr)
    mid = 0.5 * (float(prior["high"]) + float(prior["low"]))
    pdc = float(prior["close"])
    raw = [
        ("PLACEBO_MID", mid, "RESISTANCE" if _finite(open_px) and mid >= float(open_px) else "SUPPORT"),
        ("PLACEBO_PDC_UP", pdc + 0.5 * float(atr), "RESISTANCE"),
        ("PLACEBO_PDC_DN", pdc - 0.5 * float(atr), "SUPPORT"),
    ]
    out = []
    for name, center, role in raw:
        lo, hi = float(center) - hw, float(center) + hw
        if any(_overlap_bands(lo, hi, float(z["lo"]), float(z["hi"])) for z in active):
            continue
        label = "PLACEBO_RESISTANCE" if role == "RESISTANCE" else "PLACEBO_SUPPORT"
        out.append(
            {
                "zone_id": f"placebo:{name}:{round(center, 2)}",
                "role": role,
                "lo": lo,
                "hi": hi,
                "center": float(center),
                "selection_slot": name,
                "selection_label": label,
                "placebo": True,
            }
        )
    return out


def find_control(
    feat: list[dict[str, Any]],
    tf: dict[str, Any],
    *,
    by_bucket: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    def ok(x: dict[str, Any], *, bucket: str) -> bool:
        if int(x["i"]) == int(tf["i"]):
            return False
        if x.get("in_zone"):
            return False
        if str(x.get("bucket") or "") != bucket:
            return False
        if x.get("gap") != tf.get("gap"):
            return False
        if x.get("mkt_sign") != tf.get("mkt_sign"):
            return False
        if x.get("sec_sign") != tf.get("sec_sign"):
            return False
        if not _close(x.get("r5"), tf.get("r5"), MOM5_TOL):
            return False
        if not _close(x.get("r3"), tf.get("r3"), MOM3_TOL):
            return False
        if not _close(x.get("r1"), tf.get("r1"), MOM1_TOL):
            return False
        if not _rel(x.get("vol_rel"), tf.get("vol_rel"), VOL_REL_TOL):
            return False
        if not _rel(x.get("rng_rel"), tf.get("rng_rel"), RNG_REL_TOL):
            return False
        return True

    best = None
    best_d = 1e9
    buckets = [str(tf.get("bucket") or "")]
    try:
        b0 = int(buckets[0])
        buckets.extend([f"{b0 - 30:04d}", f"{b0 + 30:04d}"])
    except ValueError:
        pass
    for bi, bucket in enumerate(buckets):
        cand = (by_bucket or {}).get(bucket) if by_bucket is not None else None
        src = cand if cand is not None else feat
        for x in src:
            if not ok(x, bucket=bucket):
                continue
            d = abs(float(x["r5"]) - float(tf["r5"])) + 0.5 * abs(float(x["r3"]) - float(tf["r3"])) + 0.1 * bi
            if d < best_d:
                best_d = d
                best = x
        if best is not None:
            return best
    return best
