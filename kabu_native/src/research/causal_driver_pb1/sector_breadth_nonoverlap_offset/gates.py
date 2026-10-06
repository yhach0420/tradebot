"""Deterministic O1-O4 gate. No bootstrap, no BH, no threshold search."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.sector_breadth_nonoverlap_offset import OFFSET_ABS_RATIO, O3_FAIL_LABEL


def apply_offset_gates(
    *,
    beta_m5: float | None,
    beta_m3: float | None,
    beta_m1: float | None,
    beta_0: float | None,
    beta_k1: float | None,
    beta_k2: float | None,
    beta_k3: float | None,
    overlap_minutes_k1: int,
    overlap_minutes_k2: int,
    overlap_minutes_k3: int,
) -> dict[str, Any]:
    futures = (beta_k1, beta_k2, beta_k3)
    future_finite = all(v is not None for v in futures)
    if future_finite:
        max_future_abs = max(abs(float(v)) for v in futures if v is not None)
    else:
        max_future_abs = None
    if beta_0 is not None and max_future_abs is not None and max_future_abs > 0:
        ratio = abs(float(beta_0)) / float(max_future_abs)
    elif beta_0 is not None and max_future_abs == 0:
        ratio = None
    else:
        ratio = None
    o1 = bool(beta_0 is not None and float(beta_0) > 0.0)
    o2 = bool((beta_m1 is not None and float(beta_m1) > 0.0) or (beta_m3 is not None and float(beta_m3) > 0.0))
    o3 = bool(
        future_finite
        and beta_0 is not None
        and max_future_abs is not None
        and abs(float(beta_0)) >= float(OFFSET_ABS_RATIO) * float(max_future_abs)
    )
    o4 = bool(int(overlap_minutes_k1) == 0 and int(overlap_minutes_k2) == 0 and int(overlap_minutes_k3) == 0)
    parts: list[str] = []
    if not o1:
        parts.append("O1")
    if not o2:
        parts.append("O2")
    if not o3:
        parts.append(O3_FAIL_LABEL)
    if not o4:
        parts.append("O4")
    offset_pass = bool(o1 and o2 and o3 and o4)
    return {
        "O1": o1,
        "O2": o2,
        "O3": o3,
        "O4": o4,
        "max_future_abs": None if max_future_abs is None else float(max_future_abs),
        "abs0_to_max_future_ratio": None if ratio is None else float(ratio),
        "threshold": float(OFFSET_ABS_RATIO),
        "offset_pass": offset_pass,
        "failed_at": None if offset_pass else "+".join(parts),
        "o3_fail_label": None if o3 else O3_FAIL_LABEL,
        "beta_m5_not_an_O2_substitute": True,
        "beta_m5": None if beta_m5 is None else float(beta_m5),
    }
