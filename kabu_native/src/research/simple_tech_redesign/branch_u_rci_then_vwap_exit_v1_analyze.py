"""Full causal economics for UNPROVEN_RCI_REOVERSOLD_THEN_VWAP_LOSS_V1."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import EPS, _pf_gt, _pf_num, _tid, residual_index
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_analyze import PATH_SHORT, evaluate_cohort as _ema_evaluate_cohort
from research.simple_tech_redesign import branch_u_ema_structure_exit_v1_analyze as ema_an
from research.simple_tech_redesign.branch_u_rci_then_vwap_exit_v1_spec import CANDIDATE_EXIT_REASON, CANDIDATE_ID, RESIDUAL_CLASSES

AUDIT_CLASSES = (
    "U_EARLY_NEVER_BE",
    "P_EARLY_AFTER_BE",
    "PROTECTED_GOOD",
    "PROTECTED_DIP",
)


def evaluate_cohort(*, harvest_rows: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    prev = ema_an.CANDIDATE_EXIT_REASON
    ema_an.CANDIDATE_EXIT_REASON = CANDIDATE_EXIT_REASON
    try:
        body = _ema_evaluate_cohort(harvest_rows=harvest_rows, **kwargs)
    finally:
        ema_an.CANDIDATE_EXIT_REASON = prev
    rows_by = residual_index(harvest_rows)
    common = list(body.get("common_fills") or [])
    for rec in common:
        key = _tid(rec)
        cp = dict((rows_by.get(key) or {}).get("candidate_path") or {})
        rec["rci_armed"] = bool(cp.get("rci_armed"))
        rec["vwap_confirm"] = bool(cp.get("vwap_confirm"))
        rec["be_after_rci_before_vwap"] = bool(cp.get("be_after_rci_before_vwap"))
        rec["tech_exit"] = bool(rec.get("triggered")) or str(rec.get("exit_reason") or "") == CANDIDATE_EXIT_REASON
    class_audit = dict(body.get("class_audit") or {})
    by_cls: dict[str, list[dict[str, Any]]] = {c: [] for c in RESIDUAL_CLASSES}
    for rec in common:
        cls = str(rec.get("residual_class") or "OTHER")
        by_cls.setdefault(cls, []).append(rec)
    for name, pack in class_audit.items():
        if not isinstance(pack, dict):
            continue
        rows = by_cls.get(name) or []
        pack["positive_n"] = pack.get("positive_delta_n")
        pack["negative_n"] = pack.get("negative_delta_n")
        pack["RCI_ARM_N"] = sum(1 for r in rows if r.get("rci_armed"))
        pack["VWAP_CONFIRM_N"] = sum(1 for r in rows if r.get("vwap_confirm"))
        pack["TECH_EXIT_N"] = sum(1 for r in rows if r.get("tech_exit"))
        pack["BE_AFTER_RCI_BEFORE_VWAP_N"] = sum(1 for r in rows if r.get("be_after_rci_before_vwap"))
    body["class_audit"] = class_audit
    occ_filled = [r for r in harvest_rows if r.get("actual_filled")]
    body["RCI_ARM_N"] = sum(1 for r in occ_filled if dict(r.get("candidate_path") or {}).get("rci_armed"))
    body["VWAP_CONFIRM_N"] = sum(1 for r in occ_filled if dict(r.get("candidate_path") or {}).get("vwap_confirm"))
    body["BE_AFTER_RCI_BEFORE_VWAP_N"] = sum(
        1 for r in occ_filled if dict(r.get("candidate_path") or {}).get("be_after_rci_before_vwap")
    )
    incr = dict(body.get("incremental") or {})
    early_trades = [
        t
        for t in list(incr.get("trades") or [])
        if PATH_SHORT.get(str(t.get("path_type") or "OTHER"), "OTHER") == "EARLY"
    ]
    incr["incremental_EARLY_n"] = int(incr.get("EARLY") or 0)
    incr["incremental_EARLY_pnl"] = float(sum(float(t.get("pnl_yen_100") or 0.0) for t in early_trades))
    body["incremental"] = incr
    body["common_fills"] = common
    races = Counter(str(r.get("race_rci") or r.get("race") or "NEITHER") for r in common)
    body["RCI_FIRST_N"] = int(races.get("EVENT_FIRST") or 0) + int(races.get("RCI_FIRST") or 0)
    return body


def _pf_ge(treat: Any, ctrl: Any) -> bool:
    ta, tb = _pf_num(treat), _pf_num(ctrl)
    if ta is None or tb is None:
        return False
    return float(ta) + EPS >= float(tb)


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, harvested_ok: bool, pred_ok: bool, identity_232_ok: bool) -> dict[str, Any]:
    ident_ok = bool(dev.get("identity", {}).get("ok")) and bool(fwd.get("identity", {}).get("ok")) and bool(identity_232_ok)
    sot_ok = (
        bool(dev.get("identity", {}).get("control_sot_ok"))
        and bool(fwd.get("identity", {}).get("control_sot_ok"))
        and bool(dev.get("identity", {}).get("treatment_sot_ok"))
        and bool(fwd.get("identity", {}).get("treatment_sot_ok"))
    )
    leftover_ok = bool(dev.get("identity", {}).get("leftover_ok")) and bool(fwd.get("identity", {}).get("leftover_ok"))
    decomp_ok = bool(dev.get("decomp_ok")) and bool(fwd.get("decomp_ok"))
    be_ok = int(dev.get("identity", {}).get("be_flag_mismatch_n") or 0) == 0 and int(
        fwd.get("identity", {}).get("be_flag_mismatch_n") or 0
    ) == 0
    tf1_ok = int(dev.get("identity", {}).get("tf1_fail_n") or 0) == 0 and int(fwd.get("identity", {}).get("tf1_fail_n") or 0) == 0
    drift_ok = int(dev.get("identity", {}).get("predicate_drift_n") or 0) == 0 and int(
        fwd.get("identity", {}).get("predicate_drift_n") or 0
    ) == 0
    integrity = bool(
        leak_ok and harvested_ok and pred_ok and ident_ok and sot_ok and leftover_ok and decomp_ok and be_ok and tf1_ok and drift_ok
    )
    u_d = float(dev.get("U_EARLY_NEVER_BE_DIRECT_DELTA") or 0.0)
    p_d = float(dev.get("P_EARLY_DIRECT_DELTA") or 0.0)
    g_d = float(dev.get("GOOD_DIRECT_DELTA") or 0.0)
    dip_d = float(dev.get("DIP_DIRECT_DELTA") or 0.0)
    total = float(dev.get("TOTAL_CAUSAL_DELTA") or 0.0)
    c_pf = (dev.get("control") or {}).get("PF")
    t_pf = (dev.get("treatment") or {}).get("PF")
    c_pnl = float((dev.get("control") or {}).get("total_pnl") or 0.0)
    t_pnl = float((dev.get("treatment") or {}).get("total_pnl") or 0.0)
    c_dd = float((dev.get("control") or {}).get("max_drawdown") or 0.0)
    t_dd = float((dev.get("treatment") or {}).get("max_drawdown") or 0.0)
    pf_up = _pf_gt(t_pf, c_pf)
    pnl_up = t_pnl > c_pnl + EPS
    dd_not_worse = t_dd + EPS >= c_dd
    conc = dict(dev.get("concentration") or {})
    day_ok = not bool(conc.get("single_day_contribution_gt_50pct"))
    sym_ok = not bool(conc.get("single_symbol_contribution_gt_50pct"))
    fwd_u = float(fwd.get("U_EARLY_NEVER_BE_DIRECT_DELTA") or 0.0)
    fwd_p = float(fwd.get("P_EARLY_DIRECT_DELTA") or 0.0)
    fwd_keep = float(fwd.get("PROTECTED_KEEP_DIRECT_DELTA") or 0.0)
    fwd_total = float(fwd.get("TOTAL_CAUSAL_DELTA") or 0.0)
    fwd_pf_ok = _pf_ge((fwd.get("treatment") or {}).get("PF"), (fwd.get("control") or {}).get("PF"))
    fwd_dd = float((fwd.get("treatment") or {}).get("max_drawdown") or 0.0)
    fwd_cdd = float((fwd.get("control") or {}).get("max_drawdown") or 0.0)
    fwd_dd_ok = fwd_dd + EPS >= fwd_cdd
    fail_reverse = (u_d > EPS and fwd_u < -EPS) or (u_d < -EPS and fwd_u > EPS)
    fwd_conc = dict(fwd.get("concentration") or {})
    fwd_day_ok = not bool(fwd_conc.get("single_day_contribution_gt_50pct"))
    fwd_sym_ok = not bool(fwd_conc.get("single_symbol_contribution_gt_50pct"))
    burned_ok = (
        fwd_u + EPS >= 0.0
        and fwd_p + EPS >= 0.0
        and fwd_keep + EPS >= 0.0
        and fwd_total + EPS >= 0.0
        and fwd_pf_ok
        and fwd_dd_ok
        and (not fail_reverse)
        and fwd_day_ok
        and fwd_sym_ok
    )
    gates = {
        "1_integrity": integrity,
        "2_U_EARLY_direct_gt_0": u_d > EPS,
        "3_P_EARLY_direct_ge_0": p_d + EPS >= 0.0,
        "4_GOOD_direct_ge_0": g_d + EPS >= 0.0,
        "5_DIP_direct_ge_0": dip_d + EPS >= 0.0,
        "6_TOTAL_CAUSAL_DELTA_gt_0": total > EPS,
        "7_treatment_pnl_gt_control": pnl_up,
        "8_treatment_pf_gt_control": pf_up,
        "9_treatment_maxdd_not_worse": dd_not_worse,
        "10_single_day_contribution_le_50pct": day_ok,
        "11_single_symbol_contribution_le_50pct": sym_ok,
        "burned_u_early_ge_0": fwd_u + EPS >= 0.0,
        "burned_p_early_ge_0": fwd_p + EPS >= 0.0,
        "burned_keep_ge_0": fwd_keep + EPS >= 0.0,
        "burned_total_ge_0": fwd_total + EPS >= 0.0,
        "burned_pf_not_worse": fwd_pf_ok,
        "burned_maxdd_not_worse": fwd_dd_ok,
        "burned_failure_not_reversed": not fail_reverse,
        "burned_single_day_le_50pct": fwd_day_ok,
        "burned_single_symbol_le_50pct": fwd_sym_ok,
    }
    all_dev = all(
        [
            integrity,
            u_d > EPS,
            p_d + EPS >= 0.0,
            g_d + EPS >= 0.0,
            dip_d + EPS >= 0.0,
            total > EPS,
            pnl_up,
            pf_up,
            dd_not_worse,
            day_ok,
            sym_ok,
        ]
    )
    frozen = False
    family_closed = False
    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_BRANCH_U_RCI_VWAP_INTEGRITY_FAILED"
        nxt = "FAIL_CLOSED. Predicate, identity, BE/tie, or sequence restore failed."
    elif u_d <= EPS:
        case = "D"
        verdict = "SIMPLE_TECH_BRANCH_U_RCI_VWAP_NOT_SUPPORTED"
        nxt = (
            "U_EARLY itself is not improved. architecture CLOSE. "
            "Do not try reverse sequence or other pairs this run. Re-evaluate whether Technical EXIT development on the current inventory is still worth continuing."
        )
        family_closed = True
    elif p_d < -EPS or g_d < -EPS or dip_d < -EPS:
        case = "B"
        verdict = "SIMPLE_TECH_BRANCH_U_RCI_VWAP_RECOVERY_HARM"
        nxt = (
            "U_EARLY may improve but P_EARLY / GOOD / DIP is harmed. architecture CLOSE. "
            "Do not hop pairs this run. Re-evaluate whether Technical EXIT development on the current inventory is still worth continuing."
        )
        family_closed = True
    elif all_dev and burned_ok:
        case = "A"
        verdict = "SIMPLE_TECH_BRANCH_U_RCI_VWAP_SEQUENCE_SUPPORTED"
        nxt = (
            f"Freeze {CANDIDATE_ID}. TRUE_OOS=false CERTIFIED=false. Runtime unchanged. "
            "Do not open 20260903/04. Do not run prospective. Do not hop pairs."
        )
        frozen = True
    else:
        case = "C"
        verdict = "SIMPLE_TECH_BRANCH_U_RCI_VWAP_PORTFOLIO_FAILED"
        nxt = (
            "direct gates may pass but Full Causal / Burned failed. architecture CLOSE. "
            "Do not hop pairs this run. Re-evaluate whether Technical EXIT development on the current inventory is still worth continuing."
        )
        family_closed = True
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_FROZEN": bool(frozen),
        "FAMILY_CLOSED": bool(family_closed),
        "CANDIDATE_ID": CANDIDATE_ID,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "EXECUTION_CHANGED": False,
        "CAP_CHANGED": False,
        "SIZING_CHANGED": False,
        "AUTO_HOP": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
        "gates": gates,
        "all_development_gates": all_dev,
        "burned_direction_ok": burned_ok,
    }
