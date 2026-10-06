"""First-event sequence overlay. Sequence labels are not redefined by PnL."""
from __future__ import annotations

from typing import Any, Optional

from research.simple_tech_redesign.branch_u_false_break_rca_spec import (
    DEV_U_TRIGGER_N_EXPECTED,
    MIN_DEV_GROUP_N,
    MIN_FWD_GROUP_N,
    PRIMARY_PATH,
    SEQUENCE_LABELS,
)

EARLY = "EARLY_FAILURE"
GOOD = "GOOD_CONTINUATION"
DIP = "DIP_THEN_RECOVERY"
FWD_U_TRIGGER_N_EXPECTED = 11
EPS = 1e-12


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
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / float(len(xs))


def _sign(v: Optional[float]) -> int:
    if v is None:
        return 0
    if v > EPS:
        return 1
    if v < -EPS:
        return -1
    return 0


def _delta(r: dict[str, Any]) -> Optional[float]:
    d = _f(r.get("immediate_minus_close"))
    if d is not None:
        return d
    iu, sc = _f(r.get("immediate_u_pnl")), _f(r.get("session_close_pnl"))
    if iu is None or sc is None:
        return None
    return float(iu) - float(sc)


def overlay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    iu: list[float] = []
    sc: list[float] = []
    d: list[float] = []
    for r in rows:
        a, b = _f(r.get("immediate_u_pnl")), _f(r.get("session_close_pnl"))
        if a is not None:
            iu.append(float(a))
        if b is not None:
            sc.append(float(b))
        dd = _delta(r)
        if dd is not None:
            d.append(float(dd))
    pos = sum(1 for x in d if x > EPS)
    neg = sum(1 for x in d if x < -EPS)
    return {
        "trade_n": len(rows),
        "immediate_u_pnl": sum(iu) if iu else None,
        "session_close_pnl": sum(sc) if sc else None,
        "immediate_minus_close": sum(d) if d else None,
        "positive_immediate_benefit_n": int(pos),
        "negative_immediate_benefit_n": int(neg),
        "median_delta": _median(d),
        "total_delta": sum(d) if d else 0.0,
        "mean_delta": _mean(d),
        "delta_n": len(d),
        "tie_delta_n": len(d) - pos - neg,
        "CORE_n": sum(1 for r in rows if str(r.get("fill_role") or "") == "CORE"),
        "ADDED_n": sum(1 for r in rows if str(r.get("fill_role") or "") == "ADDED"),
        "EARLY_n": sum(1 for r in rows if str(r.get("path_type") or "") == EARLY),
        "GOOD_n": sum(1 for r in rows if str(r.get("path_type") or "") == GOOD),
        "DIP_n": sum(1 for r in rows if str(r.get("path_type") or "") == DIP),
        "OTHER_PATH_n": sum(1 for r in rows if str(r.get("path_type") or "") not in {EARLY, GOOD, DIP}),
        "tie_at_first_n": sum(1 for r in rows if r.get("tie_at_first")),
        "immediate_exit_n": sum(1 for r in rows if r.get("actual_immediate_u_exit")),
    }


def _by_label(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lab in SEQUENCE_LABELS:
        grp = [r for r in rows if str(r.get("first_label") or "") == lab]
        out[lab] = overlay(grp)
    unknown = [r for r in rows if str(r.get("first_label") or "") not in SEQUENCE_LABELS]
    if unknown:
        out["UNLABELED"] = overlay(unknown)
    return out


def _slice_rows(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    if kind == "ALL":
        return list(rows)
    if kind == "EARLY_FAILURE":
        return [r for r in rows if str(r.get("path_type") or "") == EARLY]
    if kind == "CORE":
        return [r for r in rows if str(r.get("fill_role") or "") == "CORE"]
    if kind == "ADDED":
        return [r for r in rows if str(r.get("fill_role") or "") == "ADDED"]
    if kind == "OTHER_PATH":
        return [r for r in rows if str(r.get("path_type") or "") != EARLY]
    if kind == "EARLY_CORE":
        return [r for r in rows if str(r.get("path_type") or "") == EARLY and str(r.get("fill_role") or "") == "CORE"]
    if kind == "EARLY_ADDED":
        return [r for r in rows if str(r.get("path_type") or "") == EARLY and str(r.get("fill_role") or "") == "ADDED"]
    return list(rows)


def questions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    worse = [r for r in rows if (_delta(r) is not None and float(_delta(r)) < -EPS)]
    better = [r for r in rows if (_delta(r) is not None and float(_delta(r)) > EPS)]
    reclaim = [r for r in rows if str(r.get("first_label") or "") == "RECLAIM_FIRST"]
    adverse = [r for r in rows if str(r.get("first_label") or "") == "ADVERSE_EXTENSION_FIRST"]
    worse_reclaim = [r for r in worse if str(r.get("first_label") or "") == "RECLAIM_FIRST"]
    better_adverse = [r for r in better if str(r.get("first_label") or "") == "ADVERSE_EXTENSION_FIRST"]
    rec_base = (len(reclaim) / float(n)) if n else None
    adv_base = (len(adverse) / float(n)) if n else None
    worse_share = (len(worse_reclaim) / float(len(worse))) if worse else None
    better_share = (len(better_adverse) / float(len(better))) if better else None
    rec_med = overlay(reclaim).get("median_delta")
    adv_med = overlay(adverse).get("median_delta")
    q1 = bool(
        worse
        and rec_med is not None
        and rec_med < -EPS
        and worse_share is not None
        and rec_base is not None
        and worse_share + EPS >= rec_base
        and worse_share >= 0.5 - EPS
    )
    q2 = bool(
        better
        and adv_med is not None
        and adv_med > EPS
        and better_share is not None
        and adv_base is not None
        and better_share + EPS >= adv_base
        and better_share >= 0.5 - EPS
    )
    return {
        "Q1_immediate_worse_concentrated_in_RECLAIM_FIRST": q1,
        "Q2_immediate_better_concentrated_in_ADVERSE_EXTENSION_FIRST": q2,
        "worse_n": len(worse),
        "better_n": len(better),
        "RECLAIM_n": len(reclaim),
        "ADVERSE_n": len(adverse),
        "worse_in_RECLAIM_n": len(worse_reclaim),
        "better_in_ADVERSE_n": len(better_adverse),
        "RECLAIM_base_rate": rec_base,
        "ADVERSE_base_rate": adv_base,
        "worse_share_RECLAIM": worse_share,
        "better_share_ADVERSE": better_share,
        "RECLAIM_median_delta": rec_med,
        "ADVERSE_median_delta": adv_med,
        "GOOD_n": sum(1 for r in rows if str(r.get("path_type") or "") == GOOD),
        "DIP_n": sum(1 for r in rows if str(r.get("path_type") or "") == DIP),
        "EARLY_n": sum(1 for r in rows if str(r.get("path_type") or "") == EARLY),
        "Q4_split_without_GOOD_DIP": bool(n > 0 and sum(1 for r in rows if str(r.get("path_type") or "") in {GOOD, DIP}) == 0),
        "Q5_EARLY_internal": True,
    }


def sequence_direction(by_label: dict[str, Any], *, min_n: int) -> dict[str, Any]:
    rec = dict(by_label.get("RECLAIM_FIRST") or {})
    adv = dict(by_label.get("ADVERSE_EXTENSION_FIRST") or {})
    rec_n = int(rec.get("trade_n") or 0)
    adv_n = int(adv.get("trade_n") or 0)
    rec_med = _f(rec.get("median_delta"))
    adv_med = _f(adv.get("median_delta"))
    adequate = rec_n >= int(min_n) and adv_n >= int(min_n)
    if rec_med is None or adv_med is None:
        kind = "UNKNOWN"
    elif rec_med < -EPS and adv_med > EPS:
        kind = "HYPOTHESIS"
    elif rec_med > EPS and adv_med < -EPS:
        kind = "REVERSE"
    else:
        kind = "NO_SEPARATION"
    return {
        "RECLAIM_n": rec_n,
        "ADVERSE_n": adv_n,
        "RECLAIM_median_delta": rec_med,
        "ADVERSE_median_delta": adv_med,
        "RECLAIM_total_delta": rec.get("total_delta"),
        "ADVERSE_total_delta": adv.get("total_delta"),
        "sample_adequate": bool(adequate),
        "min_n": int(min_n),
        "direction": kind,
    }


def slice_pack(rows: list[dict[str, Any]], *, min_n: int) -> dict[str, Any]:
    by = _by_label(rows)
    return {
        "overlay": overlay(rows),
        "by_label": by,
        "questions": questions(rows),
        "direction": sequence_direction(by, min_n=min_n),
        "counts": {lab: int((by.get(lab) or {}).get("trade_n") or 0) for lab in SEQUENCE_LABELS},
    }


def cohort_pack(body: dict[str, Any], *, min_n: int) -> dict[str, Any]:
    rows = list(body.get("rows") or [])
    slices = {}
    for kind in ("ALL", "EARLY_FAILURE", "CORE", "ADDED", "OTHER_PATH", "EARLY_CORE", "EARLY_ADDED"):
        slices[kind] = slice_pack(_slice_rows(rows, kind), min_n=min_n)
    primary = slices["EARLY_FAILURE"]
    return {
        "cohort": body.get("cohort"),
        "days": list(body.get("days") or []),
        "triggered_n": len(rows),
        "leak": dict(body.get("leak") or {}),
        "PRIMARY_PATH": PRIMARY_PATH,
        "primary": primary,
        "slices": slices,
        "rows": rows,
        "bb_recompute_match_n": sum(1 for r in rows if r.get("bb_recompute_match")),
        "bb_recompute_fail_n": sum(1 for r in rows if r.get("bb_recompute_match") is False),
        "trigger_low_n": sum(1 for r in rows if _f(r.get("trigger_low")) is not None),
        "tie_at_first_n": sum(1 for r in rows if r.get("tie_at_first")),
    }


def agreement(dev: dict[str, Any], fwd: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"by_label": {}, "primary_EARLY": {}, "by_slice": {}}
    for lab in SEQUENCE_LABELS:
        dlab = dict(((dev.get("primary") or {}).get("by_label") or {}).get(lab) or {})
        flab = dict(((fwd.get("primary") or {}).get("by_label") or {}).get(lab) or {})
        ds, fs = _sign(_f(dlab.get("median_delta"))), _sign(_f(flab.get("median_delta")))
        if ds == 0 or fs == 0:
            status = "INCONCLUSIVE"
        elif ds == fs:
            status = "AGREE"
        else:
            status = "DISAGREE"
        out["by_label"][lab] = {
            "DEVELOPMENT_median_delta": dlab.get("median_delta"),
            "FORWARD_BURNED_median_delta": flab.get("median_delta"),
            "DEVELOPMENT_n": dlab.get("trade_n"),
            "FORWARD_BURNED_n": flab.get("trade_n"),
            "DEVELOPMENT_sign": ds,
            "FORWARD_BURNED_sign": fs,
            "status": status,
        }
    dd = dict((dev.get("primary") or {}).get("direction") or {})
    fd = dict((fwd.get("primary") or {}).get("direction") or {})
    out["primary_EARLY"] = {
        "DEVELOPMENT_direction": dd.get("direction"),
        "FORWARD_BURNED_direction": fd.get("direction"),
        "DEVELOPMENT_sample_adequate": dd.get("sample_adequate"),
        "FORWARD_BURNED_sample_adequate": fd.get("sample_adequate"),
        "DEVELOPMENT_RECLAIM_n": dd.get("RECLAIM_n"),
        "DEVELOPMENT_ADVERSE_n": dd.get("ADVERSE_n"),
        "FORWARD_RECLAIM_n": fd.get("RECLAIM_n"),
        "FORWARD_ADVERSE_n": fd.get("ADVERSE_n"),
        "agreement": (
            "AGREE"
            if dd.get("direction") == fd.get("direction") and dd.get("direction") not in {None, "UNKNOWN"}
            else "DISAGREE"
        ),
    }
    for kind in ("ALL", "EARLY_FAILURE", "CORE", "ADDED", "OTHER_PATH"):
        ddir = dict(((dev.get("slices") or {}).get(kind) or {}).get("direction") or {})
        fdir = dict(((fwd.get("slices") or {}).get(kind) or {}).get("direction") or {})
        out["by_slice"][kind] = {
            "DEVELOPMENT_direction": ddir.get("direction"),
            "FORWARD_BURNED_direction": fdir.get("direction"),
            "DEVELOPMENT_sample_adequate": ddir.get("sample_adequate"),
            "FORWARD_BURNED_sample_adequate": fdir.get("sample_adequate"),
            "agreement": (
                "AGREE"
                if ddir.get("direction") == fdir.get("direction") and ddir.get("direction") not in {None, "UNKNOWN"}
                else "DISAGREE"
            ),
        }
    return out


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, ni_ok: bool) -> dict[str, Any]:
    if not leak_ok or not ni_ok:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_BRANCH_U_FALSE_BREAK_SEQUENCE_INVALID",
            "NEXT": "STOP. Integrity or non-interference failed.",
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "NEW_EXIT_RULE": False,
        }
    dd = dict((dev.get("primary") or {}).get("direction") or {})
    fd = dict((fwd.get("primary") or {}).get("direction") or {})
    ddir, fdir = str(dd.get("direction") or "UNKNOWN"), str(fd.get("direction") or "UNKNOWN")
    d_ok, f_ok = bool(dd.get("sample_adequate")), bool(fd.get("sample_adequate"))
    q_dev = dict((dev.get("primary") or {}).get("questions") or {})
    q_fwd = dict((fwd.get("primary") or {}).get("questions") or {})
    all_dev = str(dict(((dev.get("slices") or {}).get("ALL") or {}).get("direction") or {}).get("direction") or "UNKNOWN")
    all_fwd = str(dict(((fwd.get("slices") or {}).get("ALL") or {}).get("direction") or {}).get("direction") or "UNKNOWN")
    q5_dev = all_dev == ddir
    q5_fwd = all_fwd == fdir
    agree = agreement(dev, fwd)
    rec_adv_agree = dict((agree.get("by_label") or {}).get("RECLAIM_FIRST") or {}).get("status")
    adv_agree = dict((agree.get("by_label") or {}).get("ADVERSE_EXTENSION_FIRST") or {}).get("status")
    reasons = []
    case = "B"
    verdict = "SIMPLE_TECH_BRANCH_U_SEQUENCE_WEAK"
    nxt = "STOP. Direction exists or sample is thin; do not create a new EXIT rule."
    if int(dev.get("triggered_n") or 0) != int(DEV_U_TRIGGER_N_EXPECTED):
        case, verdict = "E", "SIMPLE_TECH_BRANCH_U_FALSE_BREAK_SEQUENCE_INVALID"
        nxt = f"STOP. DEVELOPMENT U-trigger n={dev.get('triggered_n')} != {DEV_U_TRIGGER_N_EXPECTED}."
    elif d_ok and f_ok and ddir == "HYPOTHESIS" and fdir == "HYPOTHESIS":
        case, verdict = "A", "SIMPLE_TECH_BRANCH_U_FALSE_BREAK_SEQUENCE_SUPPORTED"
        nxt = "STOP. Sequence ordering is direction-aligned on Development and Forward-burned. One state-machine candidate may be precommitted in a later run. No EXIT implemented now."
        reasons.append("both_cohorts_hypothesis_and_sample_adequate")
    elif d_ok and f_ok and {ddir, fdir} == {"HYPOTHESIS", "REVERSE"}:
        case, verdict = "C", "SIMPLE_TECH_BRANCH_U_SEQUENCE_CONTRADICTED"
        nxt = "STOP. Development / Forward-burned sequence directions reverse. No new EXIT."
        reasons.append("cross_cohort_direction_reversal")
    elif d_ok and f_ok and ddir == "REVERSE" and fdir == "REVERSE":
        case, verdict = "C", "SIMPLE_TECH_BRANCH_U_SEQUENCE_CONTRADICTED"
        nxt = "STOP. Sequence splits opposite the false-break hypothesis on both cohorts. No new EXIT."
        reasons.append("both_cohorts_reverse")
    elif d_ok and ddir == "NO_SEPARATION":
        case, verdict = "D", "SIMPLE_TECH_BRANCH_U_EXIT_PATH_CLOSED"
        nxt = "STOP. Causal event ordering does not separate false break from terminal breakdown on Development. Close Branch U architecture. Do not retune BB."
        reasons.append("development_adequate_no_separation")
    elif d_ok and ddir == "REVERSE" and (not f_ok or fdir in {"NO_SEPARATION", "UNKNOWN"}):
        case, verdict = "C", "SIMPLE_TECH_BRANCH_U_SEQUENCE_CONTRADICTED"
        nxt = "STOP. Development sequence split reverses the false-break hypothesis. No new EXIT."
        reasons.append("development_reverse")
    elif d_ok and ddir == "HYPOTHESIS" and (not f_ok or fdir in {"NO_SEPARATION", "UNKNOWN"}):
        case, verdict = "B", "SIMPLE_TECH_BRANCH_U_SEQUENCE_WEAK"
        nxt = "STOP. Development direction exists but Forward-burned sample/consistency is insufficient. No new EXIT."
        reasons.append("dev_hypothesis_fwd_weak")
    else:
        reasons.append("sample_or_direction_insufficient")
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_EXIT_RULE": False,
        "THRESHOLD_SEARCH": False,
        "PRIMARY_PATH": PRIMARY_PATH,
        "DEVELOPMENT_direction": ddir,
        "FORWARD_BURNED_direction": fdir,
        "DEVELOPMENT_sample_adequate": d_ok,
        "FORWARD_BURNED_sample_adequate": f_ok,
        "MIN_DEV_GROUP_N": int(MIN_DEV_GROUP_N),
        "MIN_FWD_GROUP_N": int(MIN_FWD_GROUP_N),
        "DEV_U_TRIGGER_N": int(dev.get("triggered_n") or 0),
        "FWD_U_TRIGGER_N": int(fwd.get("triggered_n") or 0),
        "DEV_U_TRIGGER_N_EXPECTED": int(DEV_U_TRIGGER_N_EXPECTED),
        "FWD_U_TRIGGER_N_EXPECTED": int(FWD_U_TRIGGER_N_EXPECTED),
        "Q1_DEV": q_dev.get("Q1_immediate_worse_concentrated_in_RECLAIM_FIRST"),
        "Q2_DEV": q_dev.get("Q2_immediate_better_concentrated_in_ADVERSE_EXTENSION_FIRST"),
        "Q1_FWD": q_fwd.get("Q1_immediate_worse_concentrated_in_RECLAIM_FIRST"),
        "Q2_FWD": q_fwd.get("Q2_immediate_better_concentrated_in_ADVERSE_EXTENSION_FIRST"),
        "Q3_direction_agreement": (agree.get("primary_EARLY") or {}).get("agreement"),
        "Q4_split_without_GOOD_DIP_DEV": q_dev.get("Q4_split_without_GOOD_DIP"),
        "Q4_split_without_GOOD_DIP_FWD": q_fwd.get("Q4_split_without_GOOD_DIP"),
        "Q5_EARLY_internal_DEV": q5_dev,
        "Q5_EARLY_internal_FWD": q5_fwd,
        "Q5_EARLY_internal": bool(q5_dev and q5_fwd),
        "RECLAIM_label_agreement": rec_adv_agree,
        "ADVERSE_label_agreement": adv_agree,
        "reasons": reasons,
        "agreement": agree,
    }
