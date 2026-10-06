"""Exact D1-derived R11 predicates. Do not round machine thresholds. Do not add features."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1 import (
    DIST_VWAP_THRESHOLD,
    MINS_FROM_OPEN_THRESHOLD,
    VWAP_RECLAIM_THRESHOLD,
)
from research.native_path_state_discrimination_v1.features import mins_from_open

PREDICATES: tuple[dict[str, Any], ...] = (
    {"feature": "dist_vwap", "op": ">", "threshold": DIST_VWAP_THRESHOLD},
    {"feature": "mins_from_open", "op": "<=", "threshold": MINS_FROM_OPEN_THRESHOLD},
    {"feature": "vwap_reclaim", "op": ">", "threshold": VWAP_RECLAIM_THRESHOLD},
)

PREDICATES_T: tuple[tuple[str, str, float], ...] = tuple(
    (str(p["feature"]), str(p["op"]), float(p["threshold"])) for p in PREDICATES
)

HUMAN_DISPLAY = {
    "dist_vwap": "dist_vwap > approximately -1.6324 bps",
    "mins_from_open": "before approximately 11:25 JST (feature_bar mins_from_open <= 145.5)",
    "vwap_reclaim": "VWAP reclaim = true (encoded vwap_reclaim > 0.5)",
}

RULE = {
    "rule_id": "R11",
    "predicates": [dict(p) for p in PREDICATES],
    "exit_kind": "reclaim",
    "event_gated": True,
    "every_minute_scan_is_different_strategy": True,
}


def predicate_sha256() -> str:
    payload = [{"feature": a, "op": b, "threshold": repr(c)} for a, b, c in PREDICATES_T]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _apply(st: dict[str, Any], pred: tuple[str, str, float], med: dict[str, float]) -> bool:
    name, op, thr = pred
    v = st.get(name)
    if v is None or not _finite(v):
        v = med.get(name, 0.0)
    v = float(v)
    return v <= thr if op == "<=" else v > thr


def match_r11(row: dict[str, Any], *, med: dict[str, float]) -> bool:
    st = row.get("state") or {}
    return all(_apply(st, p, med) for p in PREDICATES_T)


def bar_reclaim(rec: dict[str, Any], i: int) -> bool:
    if i < 1:
        return False
    c0 = rec["c"][i]
    vw = rec["vw"][i]
    c1 = rec["c"][i - 1]
    vw1 = rec["vw"][i - 1]
    if not (_finite(c0) and _finite(vw) and _finite(c1) and _finite(vw1)):
        return False
    return bool(c0 > vw and c1 <= vw1)


def bar_state(rec: dict[str, Any], i: int) -> dict[str, float]:
    c0 = rec["c"][i]
    vw = rec["vw"][i]
    dist = float(c0 / vw - 1.0) if _finite(c0) and _finite(vw) and float(vw) != 0 else float("nan")
    return {
        "dist_vwap": dist,
        "mins_from_open": mins_from_open(str(rec["t"][i])),
        "vwap_reclaim": 1.0 if bar_reclaim(rec, i) else 0.0,
    }


def bar_matches_r11(rec: dict[str, Any], i: int, *, med: dict[str, float]) -> bool:
    return match_r11({"state": bar_state(rec, i)}, med=med)
