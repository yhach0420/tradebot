"""V9 join-only from V8 TF1 rows. Attach CROSS/SLOPE bits. No recapture."""
from __future__ import annotations

from typing import Any

from research.simple_tech_entry_family.harvest import CACHE
from research.simple_tech_entry_family.v8_harvest import V8_CACHE
from research.simple_tech_entry_family.v9_spec import ARM_ORDER, STATE_ORDER

V9_CACHE = CACHE / "v9_trend_context_rca"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def attach_bits(row: dict[str, Any]) -> dict[str, Any]:
    tq1 = row.get("TQ1")
    tq2 = row.get("TQ2")
    cross = bool(_finite(tq1) and float(tq1) > 0.0) if _finite(tq1) else bool(row.get("ema9_gt_ema21"))
    slope = bool(_finite(tq2) and float(tq2) > 0.0)
    out = dict(row)
    out["t_cross"] = cross
    out["t_slope"] = slope
    out["current_trend"] = bool(cross and slope)
    out["trend_bit_mismatch"] = bool(bool(row.get("trend")) != bool(cross and slope))
    return out


def base_ok(row: dict[str, Any]) -> bool:
    return bool(row.get("pullback")) and bool(row.get("rci")) and bool(row.get("board_ok"))


def arm_pass(row: dict[str, Any], arm_id: str) -> bool:
    if not base_ok(row):
        return False
    if arm_id == "T0_NO_TREND":
        return True
    if arm_id == "T1_CROSS_ONLY":
        return bool(row.get("t_cross"))
    if arm_id == "T2_SLOPE_ONLY":
        return bool(row.get("t_slope"))
    if arm_id == "T3_BOTH_CURRENT":
        return bool(row.get("trend"))
    raise KeyError(arm_id)


def state_id(row: dict[str, Any]) -> str:
    c = 1 if row.get("t_cross") else 0
    s = 1 if row.get("t_slope") else 0
    return f"S{c}{s}"


def filter_arm(rows: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    if arm_id not in ARM_ORDER:
        raise KeyError(arm_id)
    hit = [r for r in rows if arm_pass(r, arm_id)]
    exe = [r for r in hit if r.get("executable_signal")]
    return {"ARM_ID": arm_id, "SIGNAL_N": len(hit), "EXECUTABLE_SIGNAL_N": len(exe), "rows": hit, "exe_rows": exe}


def filter_state(base_rows: list[dict[str, Any]], sid: str) -> dict[str, Any]:
    if sid not in STATE_ORDER:
        raise KeyError(sid)
    hit = [r for r in base_rows if state_id(r) == sid]
    exe = [r for r in hit if r.get("executable_signal")]
    return {"ARM_ID": sid, "SIGNAL_N": len(hit), "EXECUTABLE_SIGNAL_N": len(exe), "rows": hit, "exe_rows": exe}
