"""V6 pullback-depth arms, band stability, mechanism (stage-local) and entry-edge (final). No C14."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.v3_analyze import concentration, day_rows, day_sign_counts, entry_edge_gate, horizon_pack
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC
from research.simple_tech_entry_family.v6_pullback_spec import (
    ADJACENT_PAIRS,
    ARM_ORDER,
    CONTROL_ID,
    DEPTH_BANDS,
    EDGE_MIN_EXECUTABLE_FRAC_OF_BOARD,
    EDGE_MIN_EXECUTABLE_N,
    SELECT_ORDER,
    STAGE_G_MIN_EXECUTABLE_N,
    band_pq1_max,
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


def pullback_pass(row: dict[str, Any], arm_id: str) -> bool:
    if arm_id == CONTROL_ID:
        return bool(row.get("s2_v1"))
    if not _finite(row.get("PQ1")):
        return False
    if not bool(row.get("bb_close_ok")):
        return False
    return float(row["PQ1"]) <= float(band_pq1_max(arm_id)) + 1e-12


def filter_arm(pre_rows: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    pb = [r for r in pre_rows if pullback_pass(r, arm_id)]
    stage_exe = [r for r in pb if r.get("executable_signal")]
    if arm_id == CONTROL_ID:
        rci = [r for r in pb if r.get("s3_v1")]
        pa = [r for r in rci if r.get("s4_v1")]
        vol = [r for r in pa if r.get("s5_v1")]
        board = [r for r in vol if r.get("s6_v1")]
    else:
        rci = [r for r in pb if r.get("rci_ok")]
        pa = [r for r in rci if r.get("pa_ok")]
        vol = [r for r in pa if r.get("vol_ok")]
        board = [r for r in vol if r.get("board_ok")]
    final_exe = [r for r in board if r.get("executable_signal")]
    return {
        "ARM_ID": arm_id,
        "PRE_PULLBACK_N": len(pre_rows),
        "PULLBACK_PASS_N": len(pb),
        "STAGE_EXECUTABLE_N": len(stage_exe),
        "RCI_PASS_N": len(rci),
        "PRICE_ACTION_PASS_N": len(pa),
        "VOLUME_PASS_N": len(vol),
        "BOARD_PASS_N": len(board),
        "EXECUTABLE_SIGNAL_N": len(final_exe),
        "stage_rows": pb,
        "stage_exe_rows": stage_exe,
        "final_exe_rows": final_exe,
        "board_rows": board,
    }


def _fix_cost(pack: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(pack)
    has = any(r.get("cost_recovered_60") is True or r.get("cost_recovered_60") is False for r in rows)
    if not has:
        for h in HORIZONS_SEC:
            out[f"COST_RECOVERY_RATE_{int(h)}"] = None
    return out


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


def _level_metrics(exe: list[dict[str, Any]], days: list[str], prefix: str) -> dict[str, Any]:
    pack = _fix_cost(horizon_pack(exe), exe)
    daily = day_rows(exe, days)
    signs: dict[int, dict[str, Any]] = {}
    conc: dict[int, dict[str, Any]] = {}
    syms: dict[int, dict[str, Any]] = {}
    for h in (30, 60, 180, 300):
        mk = f"MARKOUT{h}_MEAN"
        signs[h] = day_sign_counts(daily, mk)
        conc[h] = concentration(exe, f"markout_{h}")
        syms[h] = symbol_pack(exe, f"markout_{h}")
    out: dict[str, Any] = {
        f"{prefix}EXECUTABLE_N": len(exe),
        f"{prefix}horizon": pack,
        f"{prefix}daily": daily,
        f"{prefix}day_sign": signs,
        f"{prefix}concentration": conc,
        f"{prefix}symbol": syms,
        f"{prefix}POSITIVE_DAY_N_180": signs[180].get("POSITIVE_DAY_N"),
        f"{prefix}NEGATIVE_DAY_N_180": signs[180].get("NEGATIVE_DAY_N"),
        f"{prefix}ZERO_DAY_N_180": signs[180].get("ZERO_DAY_N"),
        f"{prefix}POSITIVE_DAY_N_300": signs[300].get("POSITIVE_DAY_N"),
        f"{prefix}NEGATIVE_DAY_N_300": signs[300].get("NEGATIVE_DAY_N"),
        f"{prefix}ZERO_DAY_N_300": signs[300].get("ZERO_DAY_N"),
        f"{prefix}BEST_DAY_180": conc[180].get("BEST_DAY"),
        f"{prefix}EX_BEST_DAY_MARKOUT_180": conc[180].get("EX_BEST_DAY_MARKOUT"),
        f"{prefix}EX_TOP3_DAY_MARKOUT_180": conc[180].get("EX_TOP3_DAY_MARKOUT"),
        f"{prefix}BEST_DAY_300": conc[300].get("BEST_DAY"),
        f"{prefix}EX_BEST_DAY_MARKOUT_300": conc[300].get("EX_BEST_DAY_MARKOUT"),
        f"{prefix}EX_TOP3_DAY_MARKOUT_300": conc[300].get("EX_TOP3_DAY_MARKOUT"),
        f"{prefix}TOP_SYMBOL": syms[180].get("TOP_SYMBOL"),
        f"{prefix}DROP_TOP_SYMBOL_MARKOUT_180": syms[180].get("DROP_TOP_SYMBOL_MARKOUT"),
        f"{prefix}DROP_TOP_SYMBOL_MARKOUT_300": syms[300].get("DROP_TOP_SYMBOL_MARKOUT"),
        f"{prefix}exe_rows": exe,
    }
    for h in HORIZONS_SEC:
        hh = int(h)
        out[f"{prefix}MARKOUT_{hh}_MEAN"] = pack.get(f"MARKOUT_{hh}_MEAN")
        out[f"{prefix}MARKOUT_{hh}_MEDIAN"] = pack.get(f"MARKOUT_{hh}_MEDIAN")
        out[f"{prefix}MARKOUT_{hh}_POS_RATE"] = pack.get(f"MARKOUT_{hh}_POS_RATE")
        out[f"{prefix}COST_RECOVERY_RATE_{hh}"] = pack.get(f"COST_RECOVERY_RATE_{hh}")
    out[f"{prefix}MFE_MEAN"] = pack.get("MFE_MEAN")
    out[f"{prefix}MFE_MEDIAN"] = pack.get("MFE_MEDIAN")
    out[f"{prefix}MAE_MEAN"] = pack.get("MAE_MEAN")
    out[f"{prefix}MAE_MEDIAN"] = pack.get("MAE_MEDIAN")
    return out


def arm_metrics(funnel: dict[str, Any], days: list[str]) -> dict[str, Any]:
    stage = _level_metrics(list(funnel.get("stage_exe_rows") or []), days, "STAGE_")
    final = _level_metrics(list(funnel.get("final_exe_rows") or []), days, "FINAL_")
    out = {
        "ARM_ID": funnel.get("ARM_ID"),
        "PRE_PULLBACK_N": funnel.get("PRE_PULLBACK_N"),
        "PULLBACK_PASS_N": funnel.get("PULLBACK_PASS_N"),
        "STAGE_EXECUTABLE_N": funnel.get("STAGE_EXECUTABLE_N"),
        "RCI_PASS_N": funnel.get("RCI_PASS_N"),
        "PRICE_ACTION_PASS_N": funnel.get("PRICE_ACTION_PASS_N"),
        "VOLUME_PASS_N": funnel.get("VOLUME_PASS_N"),
        "BOARD_PASS_N": funnel.get("BOARD_PASS_N"),
        "EXECUTABLE_SIGNAL_N": funnel.get("EXECUTABLE_SIGNAL_N"),
    }
    out.update(stage)
    out.update(final)
    # aliases used by vs_control / edge on the intended level
    for k in (
        "MARKOUT_180_MEAN",
        "MARKOUT_300_MEAN",
        "MARKOUT_180_MEDIAN",
        "MARKOUT_300_MEDIAN",
        "MARKOUT_60_MEAN",
        "MARKOUT_60_MEDIAN",
        "EX_BEST_DAY_MARKOUT_180",
        "EX_BEST_DAY_MARKOUT_300",
        "TOP_SYMBOL",
    ):
        out[k] = out.get(f"STAGE_{k}")
    out["exe_rows"] = out.get("STAGE_exe_rows")
    out["daily"] = out.get("STAGE_daily")
    return out


def day_improve_vs_control(arm: dict[str, Any], ctrl: dict[str, Any], h: int) -> dict[str, Any]:
    mk = f"MARKOUT{h}_MEAN"
    by_a = {str(r.get("date")): r for r in (arm.get("STAGE_daily") or [])}
    by_c = {str(r.get("date")): r for r in (ctrl.get("STAGE_daily") or [])}
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


def coverage_ok(arm: dict[str, Any], *, min_n: int, min_frac: float | None = None, exe_key: str, board_key: str) -> bool:
    exe = int(arm.get(exe_key) or 0)
    board = int(arm.get(board_key) or 0)
    if exe < int(min_n):
        return False
    if min_frac is not None:
        if board <= 0:
            return False
        if float(exe) / float(board) < float(min_frac) - 1e-12:
            return False
    return True


def vs_control_gates(arm: dict[str, Any], ctrl: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    a = _gt(arm.get("STAGE_MARKOUT_180_MEAN"), ctrl.get("STAGE_MARKOUT_180_MEAN"))
    b = _gt(arm.get("STAGE_MARKOUT_300_MEAN"), ctrl.get("STAGE_MARKOUT_300_MEAN"))
    c = _gt(arm.get("STAGE_MARKOUT_180_MEDIAN"), ctrl.get("STAGE_MARKOUT_180_MEDIAN")) and _gt(
        arm.get("STAGE_MARKOUT_300_MEDIAN"), ctrl.get("STAGE_MARKOUT_300_MEDIAN")
    )
    d180 = day_improve_vs_control(arm, ctrl, 180)
    d300 = day_improve_vs_control(arm, ctrl, 300)
    d = bool(d180.get("MULTI_DAY_IMPROVE") and d300.get("MULTI_DAY_IMPROVE"))
    e = _gt(arm.get("STAGE_EX_BEST_DAY_MARKOUT_180"), ctrl.get("STAGE_EX_BEST_DAY_MARKOUT_180")) and _gt(
        arm.get("STAGE_EX_BEST_DAY_MARKOUT_300"), ctrl.get("STAGE_EX_BEST_DAY_MARKOUT_300")
    )
    top = str(arm.get("STAGE_TOP_SYMBOL") or "")
    arm_rows = arm.get("STAGE_exe_rows") or []
    ctrl_rows = ctrl.get("STAGE_exe_rows") or []
    arm_drop_180 = drop_symbol_mean(arm_rows, top, "markout_180") if top else arm.get("STAGE_MARKOUT_180_MEAN")
    ctrl_drop_180 = drop_symbol_mean(ctrl_rows, top, "markout_180") if top else ctrl.get("STAGE_MARKOUT_180_MEAN")
    arm_drop_300 = drop_symbol_mean(arm_rows, top, "markout_300") if top else arm.get("STAGE_MARKOUT_300_MEAN")
    ctrl_drop_300 = drop_symbol_mean(ctrl_rows, top, "markout_300") if top else ctrl.get("STAGE_MARKOUT_300_MEAN")
    if ctrl_drop_180 is None:
        ctrl_drop_180 = ctrl.get("STAGE_MARKOUT_180_MEAN")
    if ctrl_drop_300 is None:
        ctrl_drop_300 = ctrl.get("STAGE_MARKOUT_300_MEAN")
    f = _gt(arm_drop_180, ctrl_drop_180) and _gt(arm_drop_300, ctrl_drop_300)
    g = coverage_ok(arm, min_n=int(STAGE_G_MIN_EXECUTABLE_N), exe_key="STAGE_EXECUTABLE_N", board_key="PULLBACK_PASS_N")
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
    d180 = arm.get("FINAL_day_sign") or {}
    d300 = arm.get("FINAL_day_sign") or {}
    c180 = (arm.get("FINAL_concentration") or {}).get(180) or {}
    c300 = (arm.get("FINAL_concentration") or {}).get(300) or {}
    pack = arm.get("FINAL_horizon") or {}
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
        exe_key="EXECUTABLE_SIGNAL_N",
        board_key="BOARD_PASS_N",
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
    for aid in SELECT_ORDER:
        a = arms[aid]
        dirs[aid] = {
            "mean180_gt_control": _gt(a.get("STAGE_MARKOUT_180_MEAN"), ctrl.get("STAGE_MARKOUT_180_MEAN")),
            "mean300_gt_control": _gt(a.get("STAGE_MARKOUT_300_MEAN"), ctrl.get("STAGE_MARKOUT_300_MEAN")),
            "median180_gt_control": _gt(a.get("STAGE_MARKOUT_180_MEDIAN"), ctrl.get("STAGE_MARKOUT_180_MEDIAN")),
            "median300_gt_control": _gt(a.get("STAGE_MARKOUT_300_MEDIAN"), ctrl.get("STAGE_MARKOUT_300_MEDIAN")),
            "vs_control_supported": bool((vs.get(aid) or {}).get("SUPPORTED_VS_CONTROL")),
            "STAGE_MARKOUT_180_MEAN": a.get("STAGE_MARKOUT_180_MEAN"),
            "STAGE_MARKOUT_300_MEAN": a.get("STAGE_MARKOUT_300_MEAN"),
            "FINAL_MARKOUT_180_MEAN": a.get("FINAL_MARKOUT_180_MEAN"),
            "FINAL_MARKOUT_300_MEAN": a.get("FINAL_MARKOUT_300_MEAN"),
        }
    adjacent = []
    for left, right in ADJACENT_PAIRS:
        both_mean = bool(
            dirs[left]["mean180_gt_control"]
            and dirs[left]["mean300_gt_control"]
            and dirs[right]["mean180_gt_control"]
            and dirs[right]["mean300_gt_control"]
        )
        both_full = bool(dirs[left]["vs_control_supported"] and dirs[right]["vs_control_supported"])
        adjacent.append({"pair": [left, right], "both_mean_improve_180_300": both_mean, "both_vs_control_supported": both_full})
    d10_bad = not (dirs["D10"]["mean180_gt_control"] and dirs["D10"]["mean300_gt_control"])
    d20_good = bool(dirs["D20"]["mean180_gt_control"] and dirs["D20"]["mean300_gt_control"])
    d30_bad = not (dirs["D30"]["mean180_gt_control"] and dirs["D30"]["mean300_gt_control"])
    isolated_d20 = bool(d10_bad and d20_good and d30_bad)
    mean_only = [aid for aid in SELECT_ORDER if dirs[aid]["mean180_gt_control"] and dirs[aid]["mean300_gt_control"]]
    return {
        "D10_vs_CONTROL": dirs["D10"],
        "D20_vs_CONTROL": dirs["D20"],
        "D30_vs_CONTROL": dirs["D30"],
        "adjacent": adjacent,
        "isolated_d20_optimum": isolated_d20,
        "mean_improve_180_300_bands": mean_only,
        "monotonic_180": [arms[a].get("STAGE_MARKOUT_180_MEAN") for a in SELECT_ORDER],
        "monotonic_300": [arms[a].get("STAGE_MARKOUT_300_MEAN") for a in SELECT_ORDER],
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
            "VERDICT": "SIMPLE_TECH_V6_INTEGRITY_FAILED",
            "PULLBACK_DEPTH_MECHANISM_SUPPORTED": False,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PULLBACK_RULE": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V6": "INTEGRITY_FAILURE",
            "NEXT": "Fix integrity. Do not add depth thresholds. Do not switch to PQ3. Do not implement EXIT.",
        }
    g_any = any(bool((vs.get(aid) or {}).get("G_COVERAGE")) for aid in SELECT_ORDER)
    if int(pre_n) != int(expected_pre_n) or not g_any:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V6_PULLBACK_DEPTH_EVIDENCE_LIMITED",
            "PULLBACK_DEPTH_MECHANISM_SUPPORTED": False,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PULLBACK_RULE": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V6": "PULLBACK_DEPTH_EVIDENCE_LIMITED",
            "NEXT": "Do not pick a depth threshold. Do not switch to PQ3. Family stays open. No EXIT.",
        }
    supporting_pairs = [tuple(p["pair"]) for p in (stability.get("adjacent") or []) if p.get("both_vs_control_supported")]
    mean_pairs = [tuple(p["pair"]) for p in (stability.get("adjacent") or []) if p.get("both_mean_improve_180_300")]
    mech = bool(supporting_pairs)
    pair_bands = {b for pair in supporting_pairs for b in pair}
    edge_ok = {aid: bool((edges.get(aid) or {}).get("ENTRY_SIGNAL_EDGE_REPAIRED")) for aid in SELECT_ORDER}
    selectable = [aid for aid in SELECT_ORDER if aid in pair_bands and edge_ok[aid]]
    selected = selectable[0] if selectable else "NONE"
    family_edge = bool(selectable)
    mean_bands = list(stability.get("mean_improve_180_300_bands") or [])
    mean_pair_bands = {b for pair in mean_pairs for b in pair}
    isolated_mean = [aid for aid in mean_bands if aid not in mean_pair_bands]
    isolated = bool(isolated_mean) or bool(stability.get("isolated_d20_optimum"))
    if mech and family_edge:
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V6_PULLBACK_DEPTH_EDGE_REPAIRED",
            "PULLBACK_DEPTH_MECHANISM_SUPPORTED": True,
            "ENTRY_SIGNAL_EDGE_REPAIRED": True,
            "SELECTED_PULLBACK_RULE": selected,
            "PRIMARY_DEFICIENCY_AFTER_V6": "NONE",
            "NEXT": (
                f"Pullback candidate freeze={selected}. EMA/BB/RCI/Volume/Board stay frozen. "
                "Next: ENTRY structure verification. Do not switch to PQ3. EXIT forbidden. Runtime/CERTIFIED forbidden."
            ),
            "supporting_pairs": [list(p) for p in supporting_pairs],
        }
    if mech and not family_edge:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V6_PULLBACK_DEPTH_HELPFUL_ENTRY_STILL_INSUFFICIENT",
            "PULLBACK_DEPTH_MECHANISM_SUPPORTED": True,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PULLBACK_RULE": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V6": "ENTRY_SIGNAL_INSUFFICIENT_AFTER_PULLBACK_DEPTH",
            "NEXT": (
                "Pullback depth deficiency is real at stage-local, but depth alone does not complete ENTRY edge. "
                "STOP. Do not auto-rotate to PQ3. Do not add -7/-15/-25 bands. Architecture decision next. EXIT forbidden."
            ),
            "supporting_pairs": [list(p) for p in supporting_pairs],
        }
    if isolated:
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V6_PULLBACK_DEPTH_FRAGILE",
            "PULLBACK_DEPTH_MECHANISM_SUPPORTED": False,
            "ENTRY_SIGNAL_EDGE_REPAIRED": False,
            "SELECTED_PULLBACK_RULE": "NONE",
            "PRIMARY_DEFICIENCY_AFTER_V6": "PULLBACK_DEPTH_THRESHOLD_FRAGILE",
            "NEXT": "Do not adopt a depth threshold. Do not switch to PQ3. Family stays open. No EXIT.",
            "isolated_mean_improve_bands": isolated_mean,
        }
    return {
        "CASE": "D",
        "VERDICT": "SIMPLE_TECH_V6_PULLBACK_DEPTH_RULE_NOT_SUPPORTED",
        "PULLBACK_DEPTH_MECHANISM_SUPPORTED": False,
        "ENTRY_SIGNAL_EDGE_REPAIRED": False,
        "SELECTED_PULLBACK_RULE": "NONE",
        "PRIMARY_DEFICIENCY_AFTER_V6": "PULLBACK_DEPTH_HARD_GATE_NOT_CONFIRMED",
        "NEXT": (
            "PQ1 was not confirmed as a hard depth gate. STOP. Do not add bands. "
            "Do not auto-rotate to PQ3. Architecture decision next. EXIT forbidden."
        ),
    }


def slim_arm(arm: dict[str, Any]) -> dict[str, Any]:
    skip = {
        "STAGE_horizon",
        "STAGE_daily",
        "STAGE_day_sign",
        "STAGE_concentration",
        "STAGE_symbol",
        "STAGE_exe_rows",
        "FINAL_horizon",
        "FINAL_daily",
        "FINAL_day_sign",
        "FINAL_concentration",
        "FINAL_symbol",
        "FINAL_exe_rows",
        "exe_rows",
        "daily",
        "board_rows",
        "stage_rows",
        "stage_exe_rows",
        "final_exe_rows",
    }
    return {k: v for k, v in arm.items() if k not in skip}


_ = ARM_ORDER
_ = DEPTH_BANDS
