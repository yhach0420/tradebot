"""V4 persistence-rule arms, band stability, mechanism and entry-edge gates. No C14."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.v3_analyze import (
    concentration,
    day_rows,
    day_sign_counts,
    entry_edge_gate,
    horizon_pack,
)
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC
from research.simple_tech_entry_family.v4_persistence_spec import (
    ADJACENT_PAIRS,
    ARM_ORDER,
    CONTROL_ID,
    CONTROL_VOLUME_MULT,
    EDGE_MIN_EXECUTABLE_FRAC_OF_BOARD,
    EDGE_MIN_EXECUTABLE_N,
    MECHANISM_G_MIN_EXECUTABLE_N,
    PERSISTENCE_BANDS,
    band_min,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if _finite(x)]
    if not vs:
        return None
    return float(np.mean(vs))


def _gt(a: Any, b: Any) -> bool:
    return a is not None and b is not None and float(a) > float(b)


def volume_pass(row: dict[str, Any], arm_id: str) -> bool:
    if arm_id == CONTROL_ID:
        return bool(row.get("s5_v1"))
    return _finite(row.get("VQ2")) and float(row["VQ2"]) >= float(band_min(arm_id))


def filter_arm(pre_rows: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    vol = [r for r in pre_rows if volume_pass(r, arm_id)]
    board = [r for r in vol if r.get("board_ok")]
    exe = [r for r in board if r.get("executable_signal")]
    return {
        "ARM_ID": arm_id,
        "PRE_VOLUME_N": len(pre_rows),
        "VOLUME_PASS_N": len(vol),
        "BOARD_PASS_N": len(board),
        "EXECUTABLE_SIGNAL_N": len(exe),
        "volume_rows": vol,
        "board_rows": board,
        "exe_rows": exe,
    }


def symbol_pack(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r.get("symbol") or "")] += 1
    ordered = sorted(counts.keys(), key=lambda s: (-counts[s], s))
    top = ordered[0] if ordered else None
    top3 = ordered[:3]
    top_share = (float(counts[top]) / float(n)) if top and n else None
    top3_share = (float(sum(counts[s] for s in top3)) / float(n)) if top3 and n else None
    dropped = [r for r in rows if str(r.get("symbol") or "") != str(top)] if top else list(rows)
    return {
        "TOP_SYMBOL": top,
        "TOP_SYMBOL_SHARE": top_share,
        "TOP3_SYMBOL_SHARE": top3_share,
        "DROP_TOP_SYMBOL_MARKOUT": _mean([r.get(key) for r in dropped]),
        "DROP_TOP_SYMBOL_N": len(dropped),
    }


def drop_symbol_mean(rows: list[dict[str, Any]], symbol: str, key: str) -> Optional[float]:
    rest = [r for r in rows if str(r.get("symbol") or "") != str(symbol)]
    return _mean([r.get(key) for r in rest])


def arm_metrics(funnel: dict[str, Any], days: list[str]) -> dict[str, Any]:
    exe = list(funnel.get("exe_rows") or [])
    pack = horizon_pack(exe)
    daily = day_rows(exe, days)
    signs: dict[int, dict[str, Any]] = {}
    conc: dict[int, dict[str, Any]] = {}
    syms: dict[int, dict[str, Any]] = {}
    for h in (30, 60, 180, 300):
        mk = f"MARKOUT{h}_MEAN"
        signs[h] = day_sign_counts(daily, mk)
        conc[h] = concentration(exe, f"markout_{h}")
        syms[h] = symbol_pack(exe, f"markout_{h}")
    out = {
        "ARM_ID": funnel.get("ARM_ID"),
        "PRE_VOLUME_N": funnel.get("PRE_VOLUME_N"),
        "VOLUME_PASS_N": funnel.get("VOLUME_PASS_N"),
        "BOARD_PASS_N": funnel.get("BOARD_PASS_N"),
        "EXECUTABLE_SIGNAL_N": funnel.get("EXECUTABLE_SIGNAL_N"),
        "horizon": pack,
        "daily": daily,
        "day_sign": signs,
        "concentration": conc,
        "symbol": syms,
        "SIGNAL_DAY_N_180": signs[180].get("DAY_N_WITH_SIGNALS"),
        "POSITIVE_DAY_N_180": signs[180].get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N_180": signs[180].get("NEGATIVE_DAY_N"),
        "ZERO_DAY_N_180": signs[180].get("ZERO_DAY_N"),
        "DAY_MEDIAN_MARKOUT_180": signs[180].get("DAY_MEDIAN_MARKOUT"),
        "BEST_DAY_180": conc[180].get("BEST_DAY"),
        "EX_BEST_DAY_MARKOUT_180": conc[180].get("EX_BEST_DAY_MARKOUT"),
        "EX_TOP3_DAY_MARKOUT_180": conc[180].get("EX_TOP3_DAY_MARKOUT"),
        "SIGNAL_DAY_N_60": signs[60].get("DAY_N_WITH_SIGNALS"),
        "POSITIVE_DAY_N_60": signs[60].get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N_60": signs[60].get("NEGATIVE_DAY_N"),
        "SIGNAL_DAY_N_300": signs[300].get("DAY_N_WITH_SIGNALS"),
        "POSITIVE_DAY_N_300": signs[300].get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N_300": signs[300].get("NEGATIVE_DAY_N"),
        "ZERO_DAY_N_300": signs[300].get("ZERO_DAY_N"),
        "DAY_MEDIAN_MARKOUT_300": signs[300].get("DAY_MEDIAN_MARKOUT"),
        "BEST_DAY_300": conc[300].get("BEST_DAY"),
        "EX_BEST_DAY_MARKOUT_300": conc[300].get("EX_BEST_DAY_MARKOUT"),
        "EX_TOP3_DAY_MARKOUT_300": conc[300].get("EX_TOP3_DAY_MARKOUT"),
        "TOP_SYMBOL": syms[180].get("TOP_SYMBOL"),
        "TOP_SYMBOL_SHARE": syms[180].get("TOP_SYMBOL_SHARE"),
        "TOP3_SYMBOL_SHARE": syms[180].get("TOP3_SYMBOL_SHARE"),
        "DROP_TOP_SYMBOL_MARKOUT_180": syms[180].get("DROP_TOP_SYMBOL_MARKOUT"),
        "DROP_TOP_SYMBOL_MARKOUT_300": syms[300].get("DROP_TOP_SYMBOL_MARKOUT"),
        "exe_rows": exe,
    }
    for h in HORIZONS_SEC:
        hh = int(h)
        out[f"MARKOUT_{hh}_MEAN"] = pack.get(f"MARKOUT_{hh}_MEAN")
        out[f"MARKOUT_{hh}_MEDIAN"] = pack.get(f"MARKOUT_{hh}_MEDIAN")
        out[f"MARKOUT_{hh}_POS_RATE"] = pack.get(f"MARKOUT_{hh}_POS_RATE")
        out[f"COST_RECOVERY_RATE_{hh}"] = pack.get(f"COST_RECOVERY_RATE_{hh}")
    out["MFE_MEAN"] = pack.get("MFE_MEAN")
    out["MFE_MEDIAN"] = pack.get("MFE_MEDIAN")
    out["MAE_MEAN"] = pack.get("MAE_MEAN")
    out["MAE_MEDIAN"] = pack.get("MAE_MEDIAN")
    out["COST_RECOVERY_60"] = pack.get("COST_RECOVERY_RATE_60")
    out["COST_RECOVERY_180"] = pack.get("COST_RECOVERY_RATE_180")
    out["COST_RECOVERY_300"] = pack.get("COST_RECOVERY_RATE_300")
    return out


def day_improve_vs_control(arm: dict[str, Any], ctrl: dict[str, Any], h: int) -> dict[str, Any]:
    mk = f"MARKOUT{h}_MEAN"
    by_a = {str(r.get("date")): r for r in (arm.get("daily") or [])}
    by_c = {str(r.get("date")): r for r in (ctrl.get("daily") or [])}
    pos = neg = zero = compared = 0
    for d, ra in by_a.items():
        if not int(ra.get("SIGNAL_N") or 0):
            continue
        rc = by_c.get(d) or {}
        if not int(rc.get("SIGNAL_N") or 0):
            continue
        a = ra.get(mk)
        c = rc.get(mk)
        if not _finite(a) or not _finite(c):
            continue
        compared += 1
        if float(a) > float(c) + 1e-12:
            pos += 1
        elif float(a) < float(c) - 1e-12:
            neg += 1
        else:
            zero += 1
    return {
        "COMPARED_DAY_N": compared,
        "IMPROVE_DAY_N": pos,
        "WORSE_DAY_N": neg,
        "TIE_DAY_N": zero,
        "MULTI_DAY_IMPROVE": bool(pos >= 2 and pos > neg),
    }


def coverage_ok(arm: dict[str, Any], *, min_n: int, min_frac: float | None = None) -> bool:
    exe = int(arm.get("EXECUTABLE_SIGNAL_N") or 0)
    board = int(arm.get("BOARD_PASS_N") or 0)
    if exe < int(min_n):
        return False
    if min_frac is not None:
        if board <= 0:
            return False
        if float(exe) / float(board) < float(min_frac) - 1e-12:
            return False
    return True


def vs_control_gates(arm: dict[str, Any], ctrl: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    a = _gt(arm.get("MARKOUT_180_MEAN"), ctrl.get("MARKOUT_180_MEAN"))
    b = _gt(arm.get("MARKOUT_300_MEAN"), ctrl.get("MARKOUT_300_MEAN"))
    c = _gt(arm.get("MARKOUT_180_MEDIAN"), ctrl.get("MARKOUT_180_MEDIAN")) and _gt(
        arm.get("MARKOUT_300_MEDIAN"), ctrl.get("MARKOUT_300_MEDIAN")
    )
    d180 = day_improve_vs_control(arm, ctrl, 180)
    d300 = day_improve_vs_control(arm, ctrl, 300)
    d = bool(d180.get("MULTI_DAY_IMPROVE") and d300.get("MULTI_DAY_IMPROVE"))
    e = _gt(arm.get("EX_BEST_DAY_MARKOUT_180"), ctrl.get("EX_BEST_DAY_MARKOUT_180")) and _gt(
        arm.get("EX_BEST_DAY_MARKOUT_300"), ctrl.get("EX_BEST_DAY_MARKOUT_300")
    )
    top = str(arm.get("TOP_SYMBOL") or "")
    arm_drop_180 = drop_symbol_mean(arm.get("exe_rows") or [], top, "markout_180") if top else arm.get("MARKOUT_180_MEAN")
    ctrl_drop_180 = drop_symbol_mean(ctrl.get("exe_rows") or [], top, "markout_180") if top else ctrl.get("MARKOUT_180_MEAN")
    arm_drop_300 = drop_symbol_mean(arm.get("exe_rows") or [], top, "markout_300") if top else arm.get("MARKOUT_300_MEAN")
    ctrl_drop_300 = drop_symbol_mean(ctrl.get("exe_rows") or [], top, "markout_300") if top else ctrl.get("MARKOUT_300_MEAN")
    if ctrl_drop_180 is None:
        ctrl_drop_180 = ctrl.get("MARKOUT_180_MEAN")
    if ctrl_drop_300 is None:
        ctrl_drop_300 = ctrl.get("MARKOUT_300_MEAN")
    f = _gt(arm_drop_180, ctrl_drop_180) and _gt(arm_drop_300, ctrl_drop_300)
    g = coverage_ok(arm, min_n=int(MECHANISM_G_MIN_EXECUTABLE_N))
    h = bool(integrity_ok)
    ok = bool(a and b and c and d and e and f and g and h)
    return {
        "A_180_MEAN_GT_CONTROL": a,
        "B_300_MEAN_GT_CONTROL": b,
        "C_MEDIAN_GT_CONTROL": c,
        "D_MULTI_DAY": d,
        "E_EX_BEST": e,
        "F_DROP_TOP_SYMBOL": f,
        "G_COVERAGE": g,
        "H_INTEGRITY": h,
        "SUPPORTED_VS_CONTROL": ok,
        "day_improve_180": d180,
        "day_improve_300": d300,
        "DROP_TOP_SYMBOL": top,
        "ARM_DROP_TOP_180": arm_drop_180,
        "CTRL_DROP_TOP_180": ctrl_drop_180,
        "ARM_DROP_TOP_300": arm_drop_300,
        "CTRL_DROP_TOP_300": ctrl_drop_300,
    }


def edge_gate_arm(arm: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    d180 = arm.get("day_sign") or {}
    d300 = arm.get("day_sign") or {}
    c180 = (arm.get("concentration") or {}).get(180) or {}
    c300 = (arm.get("concentration") or {}).get(300) or {}
    pack = arm.get("horizon") or {}
    edge = entry_edge_gate(
        pack,
        d180.get(180) or {},
        d300.get(300) or {},
        c180,
        c300,
        executable_n=int(arm.get("EXECUTABLE_SIGNAL_N") or 0),
        signal_n=int(arm.get("BOARD_PASS_N") or 0),
        integrity_ok=bool(integrity_ok),
    )
    j_cov = coverage_ok(
        arm,
        min_n=int(EDGE_MIN_EXECUTABLE_N),
        min_frac=float(EDGE_MIN_EXECUTABLE_FRAC_OF_BOARD),
    )
    edge = dict(edge)
    edge["J_COVERAGE"] = bool(j_cov)
    edge["ENTRY_SIGNAL_EDGE_REPAIRED"] = bool(
        edge.get("A_MARKOUT_60_MEAN")
        and edge.get("B_MARKOUT_180_MEAN")
        and edge.get("C_MARKOUT_300_MEAN")
        and edge.get("D_MARKOUT_180_MEDIAN")
        and edge.get("E_MARKOUT_300_MEDIAN")
        and edge.get("F_POS_DAYS_180")
        and edge.get("G_POS_DAYS_300")
        and edge.get("H_EX_BEST_180")
        and edge.get("I_EX_BEST_300")
        and j_cov
        and edge.get("K_INTEGRITY")
    )
    return edge


def band_stability(arms: dict[str, dict[str, Any]], vs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ctrl = arms[CONTROL_ID]
    dirs = {}
    for aid in ("P60", "P80", "P90"):
        a = arms[aid]
        dirs[aid] = {
            "mean180_gt_control": _gt(a.get("MARKOUT_180_MEAN"), ctrl.get("MARKOUT_180_MEAN")),
            "mean300_gt_control": _gt(a.get("MARKOUT_300_MEAN"), ctrl.get("MARKOUT_300_MEAN")),
            "median180_gt_control": _gt(a.get("MARKOUT_180_MEDIAN"), ctrl.get("MARKOUT_180_MEDIAN")),
            "median300_gt_control": _gt(a.get("MARKOUT_300_MEDIAN"), ctrl.get("MARKOUT_300_MEDIAN")),
            "vs_control_supported": bool((vs.get(aid) or {}).get("SUPPORTED_VS_CONTROL")),
            "MARKOUT_180_MEAN": a.get("MARKOUT_180_MEAN"),
            "MARKOUT_300_MEAN": a.get("MARKOUT_300_MEAN"),
        }
    adjacent = []
    for left, right in ADJACENT_PAIRS:
        both_mean = bool(dirs[left]["mean180_gt_control"] and dirs[left]["mean300_gt_control"] and dirs[right]["mean180_gt_control"] and dirs[right]["mean300_gt_control"])
        both_full = bool(dirs[left]["vs_control_supported"] and dirs[right]["vs_control_supported"])
        adjacent.append({"pair": [left, right], "both_mean_improve_180_300": both_mean, "both_vs_control_supported": both_full})
    p60_bad = not (dirs["P60"]["mean180_gt_control"] and dirs["P60"]["mean300_gt_control"])
    p80_good = bool(dirs["P80"]["mean180_gt_control"] and dirs["P80"]["mean300_gt_control"])
    p90_bad = not (dirs["P90"]["mean180_gt_control"] and dirs["P90"]["mean300_gt_control"])
    isolated_p80 = bool(p60_bad and p80_good and p90_bad)
    mean_only = [aid for aid in ("P60", "P80", "P90") if dirs[aid]["mean180_gt_control"] and dirs[aid]["mean300_gt_control"]]
    return {
        "P60_vs_CONTROL": dirs["P60"],
        "P80_vs_CONTROL": dirs["P80"],
        "P90_vs_CONTROL": dirs["P90"],
        "adjacent": adjacent,
        "isolated_p80_optimum": isolated_p80,
        "mean_improve_180_300_bands": mean_only,
        "monotonic_180": [arms[a].get("MARKOUT_180_MEAN") for a in ("P60", "P80", "P90")],
        "monotonic_300": [arms[a].get("MARKOUT_300_MEAN") for a in ("P60", "P80", "P90")],
    }


def decide_case(
    *,
    vs: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    stability: dict[str, Any],
    integrity_ok: bool,
    pre_n: int,
    expected_pre_n: int,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "F",
            "VERDICT": "SIMPLE_TECH_V4_INTEGRITY_FAILED",
            "PERSISTENCE_RULE_MECHANISM_SUPPORTED": False,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PERSISTENCE_BAND": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V4": "INTEGRITY_FAILURE",
            "NEXT": "Fix integrity. Do not add thresholds. Do not implement EXIT. Do not adopt Runtime.",
        }
    g_any = any(bool((vs.get(aid) or {}).get("G_COVERAGE")) for aid in ("P60", "P80", "P90"))
    if int(pre_n) != int(expected_pre_n) or not g_any:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V4_PERSISTENCE_EVIDENCE_LIMITED",
            "PERSISTENCE_RULE_MECHANISM_SUPPORTED": False,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PERSISTENCE_BAND": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V4": "PERSISTENCE_EVIDENCE_LIMITED",
            "NEXT": "Do not pick a threshold. Family stays open. Do not implement EXIT.",
        }
    supporting_pairs = [
        tuple(p["pair"]) for p in (stability.get("adjacent") or []) if p.get("both_vs_control_supported")
    ]
    mean_pairs = [
        tuple(p["pair"]) for p in (stability.get("adjacent") or []) if p.get("both_mean_improve_180_300")
    ]
    mech = bool(supporting_pairs)
    pair_bands = {b for pair in supporting_pairs for b in pair}
    edge_ok = {aid: bool((edges.get(aid) or {}).get("ENTRY_SIGNAL_EDGE_REPAIRED")) for aid in ("P60", "P80", "P90")}
    selectable = [aid for aid in ("P60", "P80", "P90") if aid in pair_bands and edge_ok[aid]]
    selected = selectable[0] if selectable else "NONE"
    family_edge = bool(selectable)
    mean_bands = list(stability.get("mean_improve_180_300_bands") or [])
    mean_pair_bands = {b for pair in mean_pairs for b in pair}
    isolated_mean = [aid for aid in mean_bands if aid not in mean_pair_bands]
    isolated = bool(isolated_mean) or bool(stability.get("isolated_p80_optimum"))
    if mech and family_edge:
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V4_PERSISTENCE_ENTRY_EDGE_REPAIRED",
            "PERSISTENCE_RULE_MECHANISM_SUPPORTED": True,
            "ENTRY_SIGNAL_EDGE_REPAIRED": True,
            "SELECTED_PERSISTENCE_BAND": selected,
            "PRIMARY_DEFICIENCY_AFTER_V4": "NONE",
            "NEXT": (
                f"Volume candidate freeze={selected}. MA/BB/RCI/Price Action/Board stay frozen. "
                "Next: execution-neutral verification / ENTRY integrity stage. EXIT forbidden. Runtime/CERTIFIED forbidden."
            ),
            "supporting_pairs": [list(p) for p in supporting_pairs],
        }
    if mech and not family_edge:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V4_PERSISTENCE_HELPFUL_BUT_ENTRY_STILL_INSUFFICIENT",
            "PERSISTENCE_RULE_MECHANISM_SUPPORTED": True,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PERSISTENCE_BAND": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V4": "ENTRY_SIGNAL_INSUFFICIENT_AFTER_VOLUME_PERSISTENCE",
            "NEXT": (
                "Volume continuity deficiency is real but Volume alone does not complete ENTRY edge. "
                "Do not auto-rotate to RCI. Judge remaining deficiency from Trend/Reversal/Pullback plus this V4 result. "
                "No threshold add. No magnitude AND. EXIT forbidden."
            ),
            "supporting_pairs": [list(p) for p in supporting_pairs],
        }
    if isolated:
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V4_PERSISTENCE_THRESHOLD_FRAGILE",
            "PERSISTENCE_RULE_MECHANISM_SUPPORTED": False,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PERSISTENCE_BAND": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V4": "PERSISTENCE_THRESHOLD_FRAGILE",
            "NEXT": "Do not adopt a persistence threshold. Family stays open. No EXIT.",
            "isolated_mean_improve_bands": isolated_mean,
        }
    return {
        "CASE": "D",
        "VERDICT": "SIMPLE_TECH_V4_PERSISTENCE_RULE_NOT_SUPPORTED",
        "PERSISTENCE_RULE_MECHANISM_SUPPORTED": False,
        "ENTRY_SIGNAL_EDGE_REPAIRED": False,
        "SELECTED_PERSISTENCE_BAND": "NONE",
        "PRIMARY_DEFICIENCY_AFTER_V4": "PERSISTENCE_HARD_GATE_NOT_CONFIRMED",
        "NEXT": (
            "VQ2 correlation mechanism was not confirmed as a hard gate. "
            "Do not immediately negate VQ2. Do not add bands. Family stays open. No EXIT."
        ),
    }


def slim_arm(arm: dict[str, Any]) -> dict[str, Any]:
    skip = {"horizon", "daily", "day_sign", "concentration", "symbol", "exe_rows", "board_rows", "volume_rows"}
    return {k: v for k, v in arm.items() if k not in skip}


# referenced for identity
_ = ARM_ORDER
_ = PERSISTENCE_BANDS
