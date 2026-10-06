"""Board RCA comparisons. Medians / Cliff's delta / day agreement. No threshold search. No pooling of cohorts."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from research.simple_tech_redesign.causal_board_rca_spec import (
    CLIFF_MIN_ABS,
    DAY_AGREEMENT_MIN,
    EXIT_CLIFF_MIN_ABS,
    FEATURE_KEYS,
    MAX_TOP_SYMBOL_SHARE,
    MIN_EXIT_GROUP_N,
    MIN_GROUP_N,
    MIN_VARS_FOR_SUPPORT,
    PRIMARY_QUESTION,
    SECONDARY_QUESTION,
)
from research.simple_tech_redesign.v26_spec import PATH_TYPES

GOOD = "GOOD_CONTINUATION"
DIP = "DIP_THEN_RECOVERY"
EARLY = "EARLY_FAILURE"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return float(s[n // 2])
    return 0.5 * (float(s[n // 2 - 1]) + float(s[n // 2]))


def _cliffs(a: list[float], b: list[float]) -> Optional[float]:
    if not a or not b:
        return None
    pos = neg = 0
    n = 0
    for x in a:
        for y in b:
            n += 1
            if x > y:
                pos += 1
            elif x < y:
                neg += 1
    if n <= 0:
        return None
    return (pos - neg) / float(n)


def _vals(rows: list[dict[str, Any]], feat_root: str, key: str) -> list[tuple[float, str, str]]:
    out = []
    for r in rows:
        pack = dict(r.get(feat_root) or {})
        v = _f(pack.get(key))
        if v is None:
            continue
        out.append((v, str(r.get("date") or ""), str(r.get("symbol") or "")))
    return out


def compare_groups(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    feat_root: str,
    left_name: str,
    right_name: str,
) -> list[dict[str, Any]]:
    rows = []
    for key in FEATURE_KEYS:
        lv = _vals(left, feat_root, key)
        rv = _vals(right, feat_root, key)
        la = [x[0] for x in lv]
        ra = [x[0] for x in rv]
        cliff = _cliffs(la, ra)
        med_l = _median(la)
        med_r = _median(ra)
        diff = None if med_l is None or med_r is None else float(med_l) - float(med_r)
        direction = None
        if diff is None:
            direction = "NA"
        elif diff > 1e-12:
            direction = f"{left_name}_higher"
        elif diff < -1e-12:
            direction = f"{right_name}_higher"
        else:
            direction = "tie"
        by_day: dict[str, list[float]] = {}
        by_day_r: dict[str, list[float]] = {}
        for v, d, _s in lv:
            by_day.setdefault(d, []).append(v)
        for v, d, _s in rv:
            by_day_r.setdefault(d, []).append(v)
        day_signs = []
        for d in sorted(set(by_day) | set(by_day_r)):
            ml = _median(by_day.get(d) or [])
            mr = _median(by_day_r.get(d) or [])
            if ml is None or mr is None:
                continue
            if abs(ml - mr) <= 1e-12:
                day_signs.append(0)
            else:
                day_signs.append(1 if ml > mr else -1)
        overall_sign = 0 if diff is None or abs(diff) <= 1e-12 else (1 if diff > 0 else -1)
        agree = None
        if day_signs and overall_sign != 0:
            agree = sum(1 for s in day_signs if s == overall_sign) / float(len(day_signs))
        worse_side = lv if (diff is not None and diff > 0) else rv
        # concentration on the numerically higher group (often the "worse" board stress if feature is spread)
        cnt = Counter(s for _v, _d, s in worse_side)
        top_n = cnt.most_common(1)[0][1] if cnt else 0
        top_share = (top_n / float(len(worse_side))) if worse_side else None
        rows.append(
            {
                "feature": key,
                "left": left_name,
                "right": right_name,
                "n_left": len(la),
                "n_right": len(ra),
                "median_left": med_l,
                "median_right": med_r,
                "median_diff": diff,
                "direction": direction,
                "cliffs_delta": cliff,
                "day_n": len(day_signs),
                "day_agreement": agree,
                "top_symbol_share_higher_group": top_share,
                "symbol_n_higher_group": len(cnt),
            }
        )
    return rows


def _pass_pre(row: dict[str, Any], *, min_n: int, cliff_min: float) -> bool:
    if int(row.get("n_left") or 0) < min_n or int(row.get("n_right") or 0) < min_n:
        return False
    c = _f(row.get("cliffs_delta"))
    if c is None or abs(c) < float(cliff_min) - 1e-12:
        return False
    agr = _f(row.get("day_agreement"))
    if agr is None or agr + 1e-12 < float(DAY_AGREEMENT_MIN):
        return False
    sh = _f(row.get("top_symbol_share_higher_group"))
    if sh is not None and sh > float(MAX_TOP_SYMBOL_SHARE) + 1e-12:
        return False
    return True


def cohort_pack(rows: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    filled = [r for r in rows if r.get("actual_filled")]
    pos = [r for r in filled if r.get("positive")]
    neg = [r for r in filled if r.get("negative")]
    early = [r for r in filled if str(r.get("path_type") or "") == EARLY]
    good_dip = [r for r in filled if str(r.get("path_type") or "") in {GOOD, DIP}]
    core = [r for r in filled if str(r.get("fill_role") or "") == "CORE"]
    added = [r for r in filled if str(r.get("fill_role") or "") == "ADDED"]
    core_neg = [r for r in core if r.get("negative")]
    core_pos = [r for r in core if r.get("positive")]
    occ = [r for r in rows if r.get("control_occupied")]
    occ_pos = [r for r in occ if _f(r.get("control_pnl")) is not None and float(r.get("control_pnl")) > 1e-12]
    occ_neg = [r for r in occ if _f(r.get("control_pnl")) is not None and float(r.get("control_pnl")) < -1e-12]
    cap_rej = [r for r in rows if r.get("control_cap_rejected")]
    cap_acc = [r for r in rows if r.get("control_admitted")]
    u_rows = [r for r in filled if r.get("u_triggered") and r.get("exit_board")]
    recov = [r for r in u_rows if r.get("recovered_after_trigger")]
    term = [r for r in u_rows if r.get("terminal_after_trigger")]
    occ_u = [r for r in rows if r.get("treatment_u_occupied") and r.get("exit_board")]
    occ_u_rec = [r for r in occ_u if r.get("recovered_after_trigger")]
    occ_u_term = [r for r in occ_u if r.get("terminal_after_trigger")]

    pre = {
        "pos_vs_neg_unconstrained": compare_groups(pos, neg, feat_root="pre_cap", left_name="positive", right_name="negative"),
        "early_vs_gooddip": compare_groups(early, good_dip, feat_root="pre_cap", left_name="EARLY", right_name="GOOD_DIP"),
        "core_neg_vs_core_pos": compare_groups(core_neg, core_pos, feat_root="pre_cap", left_name="CORE_neg", right_name="CORE_pos"),
        "added_vs_core": compare_groups(added, core, feat_root="pre_cap", left_name="ADDED", right_name="CORE"),
        "pos_vs_neg_occupied": compare_groups(occ_pos, occ_neg, feat_root="pre_cap", left_name="occ_positive", right_name="occ_negative"),
        "cap_rejected_vs_admitted": compare_groups(cap_rej, cap_acc, feat_root="pre_cap", left_name="CAP_rejected", right_name="CAP_admitted"),
    }
    ex = {
        "recovered_vs_terminal_unconstrained": compare_groups(
            recov, term, feat_root="exit_board", left_name="RECOVERED", right_name="TERMINAL"
        ),
        "recovered_vs_terminal_occupancy_u": compare_groups(
            occ_u_rec, occ_u_term, feat_root="exit_board", left_name="RECOVERED", right_name="TERMINAL"
        ),
    }
    counts = {
        "cohort": cohort,
        "signal_n": len(rows),
        "filled_n": len(filled),
        "positive_n": len(pos),
        "negative_n": len(neg),
        "EARLY_n": len(early),
        "GOOD_n": sum(1 for r in filled if str(r.get("path_type") or "") == GOOD),
        "DIP_n": sum(1 for r in filled if str(r.get("path_type") or "") == DIP),
        "CORE_n": len(core),
        "ADDED_n": len(added),
        "occupied_n": len(occ),
        "cap_rejected_n": len(cap_rej),
        "cap_admitted_n": len(cap_acc),
        "u_triggered_n": len(u_rows),
        "recovered_n": len(recov),
        "terminal_n": len(term),
        "occupancy_u_n": len(occ_u),
        "occupancy_u_recovered_n": len(occ_u_rec),
        "occupancy_u_terminal_n": len(occ_u_term),
        "pre_cap_snapshot_ok_n": sum(1 for r in rows if dict(r.get("pre_cap") or {}).get("snapshot_ok")),
        "path_types": {p: sum(1 for r in filled if str(r.get("path_type") or "") == p) for p in PATH_TYPES},
    }
    return {"counts": counts, "pre_cap": pre, "exit_board": ex}


def _align(dev_rows: list[dict[str, Any]], fwd_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_f = {str(r.get("feature") or ""): r for r in fwd_rows}
    out = []
    for d in dev_rows:
        f = str(d.get("feature") or "")
        fw = dict(by_f.get(f) or {})
        ds = str(d.get("direction") or "")
        fs = str(fw.get("direction") or "")
        # compare left-vs-right names may differ in prefix but feature-level sign of median_diff
        dd = _f(d.get("median_diff"))
        fd = _f(fw.get("median_diff"))
        agree = False
        if dd is not None and fd is not None and abs(dd) > 1e-12 and abs(fd) > 1e-12:
            agree = (dd > 0 and fd > 0) or (dd < 0 and fd < 0)
        out.append(
            {
                "feature": f,
                "left": d.get("left"),
                "right": d.get("right"),
                "dev_n_left": d.get("n_left"),
                "dev_n_right": d.get("n_right"),
                "fwd_n_left": fw.get("n_left"),
                "fwd_n_right": fw.get("n_right"),
                "dev_median_diff": dd,
                "fwd_median_diff": fd,
                "dev_cliffs_delta": d.get("cliffs_delta"),
                "fwd_cliffs_delta": fw.get("cliffs_delta"),
                "dev_direction": ds,
                "fwd_direction": fs,
                "dev_day_agreement": d.get("day_agreement"),
                "fwd_day_agreement": fw.get("day_agreement"),
                "dev_top_symbol_share": d.get("top_symbol_share_higher_group"),
                "fwd_top_symbol_share": fw.get("top_symbol_share_higher_group"),
                "direction_agreement": agree,
                "dev_pass_pre": _pass_pre(d, min_n=MIN_GROUP_N, cliff_min=CLIFF_MIN_ABS),
                "fwd_pass_pre": _pass_pre(fw, min_n=MIN_GROUP_N, cliff_min=CLIFF_MIN_ABS) if fw else False,
                "dev_pass_exit": _pass_pre(d, min_n=MIN_EXIT_GROUP_N, cliff_min=EXIT_CLIFF_MIN_ABS),
                "fwd_pass_exit": _pass_pre(fw, min_n=MIN_EXIT_GROUP_N, cliff_min=EXIT_CLIFF_MIN_ABS) if fw else False,
                "supported_pre": bool(
                    agree
                    and _pass_pre(d, min_n=MIN_GROUP_N, cliff_min=CLIFF_MIN_ABS)
                    and _pass_pre(fw, min_n=MIN_GROUP_N, cliff_min=CLIFF_MIN_ABS)
                ),
                "supported_exit": bool(
                    agree
                    and _pass_pre(d, min_n=MIN_EXIT_GROUP_N, cliff_min=EXIT_CLIFF_MIN_ABS)
                    and _pass_pre(fw, min_n=MIN_EXIT_GROUP_N, cliff_min=EXIT_CLIFF_MIN_ABS)
                ),
            }
        )
    return out


def field_availability(inv: dict[str, Any]) -> dict[str, Any]:
    n = max(int(inv.get("events_n") or 0), 1)
    levels = {}
    for k, rec in dict(inv.get("levels") or {}).items():
        present = int(rec.get("present_n") or 0)
        levels[k] = {
            "present_n": present,
            "present_rate": present / float(n),
            "price_n": int(rec.get("price_n") or 0),
            "qty_n": int(rec.get("qty_n") or 0),
            "time_n": int(rec.get("time_n") or 0),
            "sign_n": int(rec.get("sign_n") or 0),
            "usable": present / float(n) >= 0.5,
        }
    scalars = {}
    for k, c in dict(inv.get("scalars") or {}).items():
        ci = int(c or 0)
        scalars[k] = {"present_n": ci, "present_rate": ci / float(n), "usable": ci / float(n) >= 0.5}
    true_l1 = bool(levels.get("Buy1", {}).get("usable") and levels.get("Sell1", {}).get("usable"))
    depth = all(bool(levels.get(f"Buy{i}", {}).get("usable") and levels.get(f"Sell{i}", {}).get("usable")) for i in range(1, 11))
    return {
        "sample_events_n": int(inv.get("events_n") or 0),
        "days": list(inv.get("days") or []),
        "levels": levels,
        "scalars": scalars,
        "true_l1_buy1_sell1_usable": true_l1,
        "depth_buy1to10_sell1to10_usable": depth,
        "kabu_BidPrice_usable_but_not_true_bid": bool(scalars.get("BidPrice", {}).get("usable")),
        "note": "True L1 is Buy1/Sell1. Raw BidPrice/AskPrice are kabu-inverted labels and are not used as features.",
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], availability: dict[str, Any]) -> dict[str, Any]:
    pre_compares = (
        "pos_vs_neg_unconstrained",
        "pos_vs_neg_occupied",
        "early_vs_gooddip",
        "core_neg_vs_core_pos",
        "added_vs_core",
        "cap_rejected_vs_admitted",
    )
    aligned_pre: dict[str, list[dict[str, Any]]] = {}
    pre_supported_vars = []
    for name in pre_compares:
        al = _align(list((dev.get("pre_cap") or {}).get(name) or []), list((fwd.get("pre_cap") or {}).get(name) or []))
        aligned_pre[name] = al
        for r in al:
            if r.get("supported_pre"):
                pre_supported_vars.append(f"{name}:{r.get('feature')}")
    aligned_ex: dict[str, list[dict[str, Any]]] = {}
    ex_supported_vars = []
    for name in ("recovered_vs_terminal_unconstrained", "recovered_vs_terminal_occupancy_u"):
        al = _align(list((dev.get("exit_board") or {}).get(name) or []), list((fwd.get("exit_board") or {}).get(name) or []))
        aligned_ex[name] = al
        for r in al:
            if r.get("supported_exit"):
                ex_supported_vars.append(f"{name}:{r.get('feature')}")

    pre_ok = len(pre_supported_vars) >= int(MIN_VARS_FOR_SUPPORT) and bool(availability.get("true_l1_buy1_sell1_usable"))
    ex_ok = len(ex_supported_vars) >= int(MIN_VARS_FOR_SUPPORT) and bool(availability.get("true_l1_buy1_sell1_usable"))
    if pre_ok and ex_ok:
        case = "C"
        verdict = "SIMPLE_TECH_BOARD_BOTH_INFORMATION_SUPPORTED"
        primary = PRIMARY_QUESTION
        if len(ex_supported_vars) > len(pre_supported_vars) * 2:
            primary = SECONDARY_QUESTION
    elif pre_ok:
        case = "A"
        verdict = "SIMPLE_TECH_PRE_CAP_BOARD_INFORMATION_SUPPORTED"
        primary = PRIMARY_QUESTION
    elif ex_ok:
        case = "B"
        verdict = "SIMPLE_TECH_EXIT_BOARD_CONFIRMATION_SUPPORTED"
        primary = SECONDARY_QUESTION
    else:
        case = "D"
        verdict = "SIMPLE_TECH_BOARD_INFORMATION_NOT_SUPPORTED"
        primary = None
    next_step = "STOP. Do not implement a board rule in this run. "
    if case == "D":
        next_step += "Reject the hypothesis that adding board information would stably improve Simple Tech. "
    else:
        next_step += f"If a later implementation run is opened, PRIMARY_NEXT_MECHANISM={primary} only. Burned days cannot certify it. "
    next_step += "CAP remains occupancy-only. board_ok is not revived."
    return {
        "CASE": case,
        "VERDICT": verdict,
        "PRIMARY_NEXT_MECHANISM": primary,
        "PRE_CAP_SUPPORTED": bool(pre_ok),
        "EXIT_BOARD_SUPPORTED": bool(ex_ok),
        "PRE_CAP_SUPPORTED_VARS": pre_supported_vars,
        "EXIT_SUPPORTED_VARS": ex_supported_vars,
        "ALIGNED_PRE": aligned_pre,
        "ALIGNED_EXIT": aligned_ex,
        "NEXT": next_step,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "POLICY_CREATED": False,
        "BOARD_OK_REUSED": False,
        "THRESHOLD_SEARCH": False,
        "CAP_CHANGED": False,
    }


def answers(dev: dict[str, Any], fwd: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    def _summary(name: str) -> dict[str, Any]:
        rows = list((decision.get("ALIGNED_PRE") or {}).get(name) or [])
        hits = [r for r in rows if r.get("supported_pre")]
        agrees = [r for r in rows if r.get("direction_agreement")]
        return {
            "supported_var_n": len(hits),
            "direction_agree_n": len(agrees),
            "supported_features": [r.get("feature") for r in hits],
            "agree_features": [r.get("feature") for r in agrees],
        }

    ex_rows = list((decision.get("ALIGNED_EXIT") or {}).get("recovered_vs_terminal_unconstrained") or [])
    return {
        "q1_pos_neg_board_diff": _summary("pos_vs_neg_unconstrained"),
        "q2_early_vs_gooddip": _summary("early_vs_gooddip"),
        "q3_core_failure_board": _summary("core_neg_vs_core_pos"),
        "q4_added_vs_core": _summary("added_vs_core"),
        "q5_dev_fwd_sign_agreement_pos_neg": _summary("pos_vs_neg_unconstrained"),
        "q6_day_stability_included_in_pass_rule": True,
        "q7_symbol_concentration_blocked_if_top_share_gt_50pct": True,
        "q8_cap_rejected_vs_admitted": _summary("cap_rejected_vs_admitted"),
        "q9_cap_is_not_quality_filter": (
            "CAP reject vs admit board differences are diagnostic only. "
            "CAP remains MAX_CONCURRENT_POSITION_CONSTRAINT. "
            "A difference does not mean rejected names were bad."
        ),
        "occupied_pos_neg": _summary("pos_vs_neg_occupied"),
        "exit_recovered_vs_terminal_supported_features": [r.get("feature") for r in ex_rows if r.get("supported_exit")],
        "dev_counts": dev.get("counts"),
        "fwd_counts": fwd.get("counts"),
    }
