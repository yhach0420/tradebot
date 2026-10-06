"""V9 trend-component gates, 2x2 states, TOD diagnostic. No new thresholds."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.simple_tech_entry_family.v4_analyze import _mean
from research.simple_tech_entry_family.v6_pullback_analyze import drop_symbol_mean, symbol_pack
from research.simple_tech_entry_family.v8_analyze import arm_metrics, core_edge_gate, slim_arm, vs_arm
from research.simple_tech_entry_family.v8_spec import ROLE_MIN_EXECUTABLE_N
from research.simple_tech_entry_family.v9_spec import ROLE_MIN_EXECUTABLE_N as V9_MIN

JST = ZoneInfo("Asia/Tokyo")
TOD_LABELS = ("09_10", "10_11", "11_CLOSE")


def tod_bucket(t0: Any) -> Optional[str]:
    try:
        dt = datetime.fromtimestamp(float(t0), JST)
    except (TypeError, ValueError, OSError):
        return None
    h = int(dt.hour)
    m = int(dt.minute)
    if h < 9 or (h == 9 and m < 0):
        return None
    if h == 9:
        return "09_10"
    if h == 10:
        return "10_11"
    if h >= 11:
        return "11_CLOSE"
    return None


def drop_top3_mean(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r.get("symbol") or "")] += 1
    top3 = sorted(counts.keys(), key=lambda s: (-counts[s], s))[:3]
    rest = [r for r in rows if str(r.get("symbol") or "") not in set(top3)]
    return _mean([r.get(key) for r in rest])


def concentration_extra(arm: dict[str, Any]) -> dict[str, Any]:
    exe = list(arm.get("exe_rows") or [])
    sp = symbol_pack(exe, "markout_180")
    return {
        "TOP_SYMBOL_SHARE": sp.get("TOP_SYMBOL_SHARE"),
        "TOP3_SYMBOL_SHARE": sp.get("TOP3_SYMBOL_SHARE"),
        "DROP_TOP3_180": drop_top3_mean(exe, "markout_180"),
        "DROP_TOP3_300": drop_top3_mean(exe, "markout_300"),
        "TOP3_SYMBOLS": None,
    }


def tod_pack(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        b = tod_bucket(r.get("t0"))
        if b:
            by[b].append(r)
    out = []
    n_all = len(rows)
    for lab in TOD_LABELS:
        xs = by.get(lab) or []
        exe = [r for r in xs if r.get("executable_signal")]
        out.append(
            {
                "bucket": lab,
                "N": len(xs),
                "SHARE": (len(xs) / n_all) if n_all else None,
                "EXECUTABLE_N": len(exe),
                "MARKOUT180_MEAN": _mean([r.get("markout_180") for r in exe]),
                "MARKOUT300_MEAN": _mean([r.get("markout_300") for r in exe]),
            }
        )
    return out


def state_exclusion(states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    s11 = states.get("S11") or {}
    others = {k: states[k] for k in ("S10", "S01", "S00") if k in states}

    def better(h: str) -> bool:
        a = s11.get(h)
        if a is None:
            return False
        for st in others.values():
            b = st.get(h)
            if b is None:
                return False
            if not (float(a) > float(b) + 1e-12):
                return False
        return True

    return {
        "S11_BETTER_THAN_OTHERS_180": better("MARKOUT180_MEAN"),
        "S11_BETTER_THAN_OTHERS_300": better("MARKOUT300_MEAN"),
        "EXCLUSION_OF_BAD_STATES": bool(better("MARKOUT180_MEAN") and better("MARKOUT300_MEAN")),
        "diagnostic_only": True,
    }


def joint_not_small_sample(t3: dict[str, Any], vs_t0: dict[str, Any]) -> bool:
    n = int(t3.get("EXECUTABLE_SIGNAL_N") or 0)
    share = (t3.get("TOP_SYMBOL_SHARE") if t3.get("TOP_SYMBOL_SHARE") is not None else (concentration_extra(t3).get("TOP_SYMBOL_SHARE")))
    days = int((t3.get("POSITIVE_DAY_N_180") or 0) + (t3.get("NEGATIVE_DAY_N_180") or 0))
    return bool(
        n >= int(V9_MIN)
        and n >= int(ROLE_MIN_EXECUTABLE_N)
        and days >= 5
        and bool(vs_t0.get("E_EX_BEST"))
        and bool(vs_t0.get("F_DROP_TOP_SYMBOL"))
        and (share is None or float(share) < 0.5)
    )


def edge_t3(t3: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    pack = {
        "MARKOUT60_MEAN": t3.get("MARKOUT60_MEAN"),
        "MARKOUT180_MEAN": t3.get("MARKOUT180_MEAN"),
        "MARKOUT300_MEAN": t3.get("MARKOUT300_MEAN"),
        "MARKOUT180_MEDIAN": t3.get("MARKOUT180_MEDIAN"),
        "MARKOUT300_MEDIAN": t3.get("MARKOUT300_MEDIAN"),
        "POSITIVE_DAY_N_180": t3.get("POSITIVE_DAY_N_180"),
        "NEGATIVE_DAY_N_180": t3.get("NEGATIVE_DAY_N_180"),
        "POSITIVE_DAY_N_300": t3.get("POSITIVE_DAY_N_300"),
        "NEGATIVE_DAY_N_300": t3.get("NEGATIVE_DAY_N_300"),
        "EX_BEST_180": t3.get("EX_BEST_180"),
        "EX_BEST_300": t3.get("EX_BEST_300"),
        "EXECUTABLE_SIGNAL_N": t3.get("EXECUTABLE_SIGNAL_N"),
    }
    return core_edge_gate(pack, integrity_ok=integrity_ok)


def decide_case(
    *,
    cross: bool,
    slope: bool,
    joint: bool,
    interaction: bool,
    integrity_ok: bool,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "INTEGRITY",
            "VERDICT": "SIMPLE_TECH_V9_INTEGRITY_FAILED",
            "PRIMARY_TREND_INTERPRETATION": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }
    if joint:
        interp = (
            "EMA9>EMA21 AND EMA21-rising is joint Pullback+RCI context, not a standalone direction predictor."
            if interaction
            else "Current joint Trend (cross AND slope) is a Pullback+RCI adoption filter."
        )
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V9_TREND_JOINT_CONTEXT_SUPPORTED",
            "PRIMARY_TREND_INTERPRETATION": interp,
            "NEXT": "Freeze T3 Trend form. Next: RCI and Board incremental roles inside that Trend context. Do not generalize V8 A4 vs A5/A6. No EXIT.",
        }
    if cross and not slope:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V9_EMA_ALIGNMENT_CONTEXT_SUPPORTED",
            "PRIMARY_TREND_INTERPRETATION": "EMA9>EMA21 alignment is the supported Trend context.",
            "NEXT": "Freeze CROSS-only Trend form. Next: RCI/Board incremental inside that context. No EXIT.",
        }
    if slope and not cross:
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V9_EMA_SLOPE_CONTEXT_SUPPORTED",
            "PRIMARY_TREND_INTERPRETATION": "EMA21 rising is the supported Trend context.",
            "NEXT": "Freeze SLOPE-only Trend form. Next: RCI/Board incremental inside that context. No EXIT.",
        }
    return {
        "CASE": "D",
        "VERDICT": "SIMPLE_TECH_V9_TREND_SUPPORT_NOT_REPRODUCED",
        "PRIMARY_TREND_INTERPRETATION": "V8 Trend hard-gate finding did not reproduce as CROSS, SLOPE, or JOINT vs T0.",
        "NEXT": "Re-evaluate the V8 A3 vs A4 Trend interaction. No extra arms, no EMA retune, no EXIT.",
    }
