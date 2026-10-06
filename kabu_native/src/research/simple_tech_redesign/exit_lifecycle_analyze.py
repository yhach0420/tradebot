"""Lifecycle branch frequency. No PnL selection. Path labels joined after state construction."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.exit_lifecycle_spec import (
    ADDED_FILL_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    MIN_CORE_KEEP_N,
    MIN_DAY_PAIR_N,
    MIN_DAYS,
    MIN_FAIL_N,
    MIN_KEEP_N,
    MIN_SYMBOLS,
    P_KEEP,
    P_SEQUENCE_IDS,
    PATH_TYPES,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
    U_COMPARE,
    U_SEQUENCE_IDS,
)


def _fills(rows: list[dict[str, Any]], role: Optional[str] = None) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        if role is not None and str(r.get("fill_role") or "") != role:
            continue
        out.append(r)
    return out


def _lc(r: dict[str, Any]) -> dict[str, Any]:
    return dict(r.get("lifecycle") or {})


def _path(r: dict[str, Any]) -> str:
    return str(r.get("path_type") or "OTHER")


def _hit(r: dict[str, Any], seq_id: str) -> Optional[bool]:
    pack = _lc(r)
    if seq_id.startswith("U_"):
        u = dict(pack.get("u") or {})
        v = u.get(seq_id)
        return None if v is None else bool(v)
    if seq_id.startswith("P_"):
        p = dict(pack.get("p") or {})
        if not p.get("observable"):
            return None
        v = p.get(seq_id)
        return None if v is None else bool(v)
    return None


def _rate(n: int, d: int) -> Optional[float]:
    if int(d) <= 0:
        return None
    return float(n) / float(d)


def _syms(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("symbol") or "").replace(".T", "") for r in rows}


def _days(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("date") or "") for r in rows}


def _day_sign(fail: list[dict[str, Any]], keep: list[dict[str, Any]], seq_id: str) -> dict[str, Any]:
    by: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"fail": [], "keep": []})
    for r in fail:
        v = _hit(r, seq_id)
        if v is None:
            continue
        by[str(r.get("date"))]["fail"].append(1 if v else 0)
    for r in keep:
        v = _hit(r, seq_id)
        if v is None:
            continue
        by[str(r.get("date"))]["keep"].append(1 if v else 0)
    agree = disagree = usable = 0
    daily = []
    for d in ELIGIBLE_DAYS:
        g = by.get(d) or {"fail": [], "keep": []}
        if len(g["fail"]) < int(MIN_DAY_PAIR_N) or len(g["keep"]) < int(MIN_DAY_PAIR_N):
            daily.append({"date": d, "usable": False, "fail_rate": None, "keep_rate": None, "sign": None})
            continue
        usable += 1
        rf = float(sum(g["fail"])) / float(len(g["fail"]))
        rk = float(sum(g["keep"])) / float(len(g["keep"]))
        if rf == rk:
            sign = "TIE"
        elif rf > rk:
            agree += 1
            sign = "AGREE"
        else:
            disagree += 1
            sign = "DISAGREE"
        daily.append({"date": d, "usable": True, "fail_rate": rf, "keep_rate": rk, "sign": sign})
    return {"usable_days": usable, "agree_days": agree, "disagree_days": disagree, "daily": daily}


def sequence_test(fail: list[dict[str, Any]], keep: list[dict[str, Any]], seq_id: str, *, core_keep: list[dict[str, Any]], added_fail: list[dict[str, Any]], added_keep: list[dict[str, Any]]) -> dict[str, Any]:
    def hits(rs: list[dict[str, Any]]) -> tuple[int, int]:
        vs = [v for v in (_hit(r, seq_id) for r in rs) if v is not None]
        return sum(1 for v in vs if v), len(vs)

    fh, fn = hits(fail)
    kh, kn = hits(keep)
    ckh, ckn = hits(core_keep)
    afh, afn = hits(added_fail)
    akh, akn = hits(added_keep)
    fr, kr, ckr = _rate(fh, fn), _rate(kh, kn), _rate(ckh, ckn)
    direction = fr is not None and kr is not None and float(fr) > float(kr)
    core_ok = fr is not None and ckr is not None and float(ckr) < float(fr)
    added_ok = True
    if afn >= int(MIN_FAIL_N) and akn >= 4:
        afr, akr = _rate(afh, afn), _rate(akh, akn)
        added_ok = afr is not None and akr is not None and float(afr) > float(akr)
    day = _day_sign(fail, keep, seq_id)
    floors = (
        int(fn) >= int(MIN_FAIL_N)
        and int(kn) >= int(MIN_KEEP_N)
        and int(ckn) >= int(MIN_CORE_KEEP_N)
        and len(_days(fail)) >= int(MIN_DAYS)
        and len(_days(keep)) >= int(MIN_DAYS)
        and len(_syms(fail)) >= int(MIN_SYMBOLS)
        and len(_syms(keep)) >= int(MIN_SYMBOLS)
    )
    day_ok = int(day.get("usable_days") or 0) >= int(MIN_DAYS) and int(day.get("agree_days") or 0) > int(day.get("disagree_days") or 0)
    ratio = None
    if kr is not None and fr is not None and float(kr) > 1e-12:
        ratio = float(fr) / float(kr)
    fail_hit_rows = [r for r in fail if _hit(r, seq_id) is True]
    hit_sym = Counter(str(r.get("symbol") or "").replace(".T", "") for r in fail_hit_rows)
    top_sym, top_n = (hit_sym.most_common(1)[0] if hit_sym else ("", 0))
    return {
        "seq_id": seq_id,
        "SUPPORTED": bool(floors and direction and core_ok and added_ok and day_ok),
        "floors_ok": floors,
        "direction_ok": bool(direction),
        "core_ok": bool(core_ok),
        "added_ok": bool(added_ok),
        "day_ok": bool(day_ok),
        "day": {"usable_days": day["usable_days"], "agree_days": day["agree_days"], "disagree_days": day["disagree_days"]},
        "daily": day.get("daily"),
        "fail_n": fn,
        "keep_n": kn,
        "fail_hit_n": fh,
        "keep_hit_n": kh,
        "fail_hit_rate": fr,
        "keep_hit_rate": kr,
        "bad_keep_rate_ratio": ratio,
        "core_keep_n": ckn,
        "core_keep_hit_n": ckh,
        "core_keep_hit_rate": ckr,
        "added_fail_hit_rate": _rate(afh, afn),
        "added_keep_hit_rate": _rate(akh, akn),
        "AM_N": fn + kn,
        "PM_N": 0,
        "fail_days": len(_days(fail)),
        "keep_days": len(_days(keep)),
        "fail_symbols": len(_syms(fail)),
        "keep_symbols": len(_syms(keep)),
        "fail_hit_symbol_n": len(hit_sym),
        "top_fail_hit_symbol": top_sym or None,
        "top_fail_hit_share": _rate(int(top_n), len(fail_hit_rows)),
    }


def _be_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    be_n = sum(1 for r in rows if _lc(r).get("break_even_reached"))
    return {"trade_n": n, "break_even_reached_n": be_n, "break_even_reached_rate": _rate(be_n, n), "never_break_even_n": n - be_n}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = _fills(rows)
    core = _fills(rows, "CORE")
    added = _fills(rows, "ADDED")
    by_path = {p: [r for r in fills if _path(r) == p] for p in PATH_TYPES}
    decomp = {p: _be_pack(by_path[p]) for p in PATH_TYPES}
    decomp_core = {p: _be_pack([r for r in core if _path(r) == p]) for p in PATH_TYPES}
    decomp_added = {p: _be_pack([r for r in added if _path(r) == p]) for p in PATH_TYPES}

    early = [r for r in fills if _path(r) == "EARLY_FAILURE"]
    dip = [r for r in fills if _path(r) == "DIP_THEN_RECOVERY"]
    ptf = [r for r in fills if _path(r) == "PROFIT_THEN_FAILURE"]
    keep_p = [r for r in fills if _path(r) in P_KEEP and _lc(r).get("break_even_reached")]
    ptf_p = [r for r in ptf if _lc(r).get("break_even_reached")]

    u_tests = [
        sequence_test(
            early,
            dip,
            sid,
            core_keep=[r for r in core if _path(r) == "DIP_THEN_RECOVERY"],
            added_fail=[r for r in added if _path(r) == "EARLY_FAILURE"],
            added_keep=[r for r in added if _path(r) == "DIP_THEN_RECOVERY"],
        )
        for sid in U_SEQUENCE_IDS
    ]
    p_tests = [
        sequence_test(
            ptf_p,
            keep_p,
            sid,
            core_keep=[r for r in core if _path(r) in P_KEEP and _lc(r).get("break_even_reached")],
            added_fail=[r for r in added if _path(r) == "PROFIT_THEN_FAILURE" and _lc(r).get("break_even_reached")],
            added_keep=[r for r in added if _path(r) in P_KEEP and _lc(r).get("break_even_reached")],
        )
        for sid in P_SEQUENCE_IDS
    ]
    u_supported = [t["seq_id"] for t in u_tests if t.get("SUPPORTED")]
    p_supported = [t["seq_id"] for t in p_tests if t.get("SUPPORTED")]
    u_mixed = [t["seq_id"] for t in u_tests if t.get("direction_ok") and not t.get("SUPPORTED")]
    p_mixed = [t["seq_id"] for t in p_tests if t.get("direction_ok") and not t.get("SUPPORTED")]

    trades = []
    for r in fills:
        pack = _lc(r)
        trades.append(
            {
                "date": r.get("date"),
                "symbol": str(r.get("symbol") or "").replace(".T", ""),
                "t0": r.get("t0"),
                "fill_role": r.get("fill_role"),
                "path_type": _path(r),
                "branch": pack.get("branch"),
                "break_even_reached": pack.get("break_even_reached"),
                "be_t": pack.get("be_t"),
                "peak_yen": pack.get("peak_yen"),
                "pullback_low": dict(pack.get("entry") or {}).get("pullback_low"),
                **{k: dict(pack.get("u") or {}).get(k) for k in U_SEQUENCE_IDS},
                **{k: dict(pack.get("p") or {}).get(k) for k in P_SEQUENCE_IDS},
            }
        )
    return {
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": sum(1 for r in rows if r.get("executable_signal")),
        "CORE_FILL_N": len(core),
        "ADDED_FILL_N": len(added),
        "TOTAL_RESEARCH_FILL_N": len(fills),
        "CORRECTED_FILL_HASH": set_hash(fill_tuples_e4(rows)),
        "RESEARCH_FILL_SET_HASH": set_hash(research_fill_tuples(rows)),
        "BRANCH_U_N": sum(1 for r in fills if _lc(r).get("branch") == "U"),
        "BRANCH_P_N": sum(1 for r in fills if _lc(r).get("branch") == "P"),
        "decomposition": decomp,
        "decomposition_core": decomp_core,
        "decomposition_added": decomp_added,
        "u_tests": u_tests,
        "p_tests": p_tests,
        "U_SUPPORTED": u_supported,
        "P_SUPPORTED": p_supported,
        "U_MIXED": u_mixed,
        "P_MIXED": p_mixed,
        "trades": trades,
        "fill_below_pullback_low_n": sum(1 for r in fills if dict(_lc(r).get("entry") or {}).get("fill_below_pullback_low")),
        "entry_thesis_at_signal": {
            "trend_up_n": sum(1 for r in fills if dict(_lc(r).get("entry") or {}).get("trend_up")),
            "pullback_setup_n": sum(1 for r in fills if dict(_lc(r).get("entry") or {}).get("pullback_setup")),
            "reversal_rci_n": sum(1 for r in fills if dict(_lc(r).get("entry") or {}).get("reversal_rci")),
        },
        "U_COMPARE": list(U_COMPARE),
        "P_KEEP": list(P_KEEP),
    }


def decide(summary: dict[str, Any], *, signal_parity: bool, fill_identity: bool, leak_ok: bool, ni_ok: bool) -> dict[str, Any]:
    counts_ok = (
        int(summary.get("SIGNAL_N") or 0) == int(B1_SIGNAL_N_EXPECTED)
        and int(summary.get("EXECUTION_EVALUABLE_N") or 0) == int(CORRECTED_EVALUABLE_N_EXPECTED)
        and int(summary.get("CORE_FILL_N") or 0) == int(CORE_E4_FILL_N_EXPECTED)
        and int(summary.get("ADDED_FILL_N") or 0) == int(ADDED_FILL_N_EXPECTED)
        and int(summary.get("TOTAL_RESEARCH_FILL_N") or 0) == int(TOTAL_RESEARCH_FILL_N_EXPECTED)
    )
    hash_ok = (
        str(summary.get("CORRECTED_FILL_HASH") or "") == CORRECTED_FILL_HASH_EXPECTED
        and str(summary.get("RESEARCH_FILL_SET_HASH") or "") == RESEARCH_FILL_SET_HASH_EXPECTED
        and bool(fill_identity)
    )
    integrity = bool(leak_ok and ni_ok and signal_parity and counts_ok and hash_ok)
    u_sup = list(summary.get("U_SUPPORTED") or [])
    p_sup = list(summary.get("P_SUPPORTED") or [])
    u_mixed = list(summary.get("U_MIXED") or [])
    p_mixed = list(summary.get("P_MIXED") or [])
    if not integrity:
        case, verdict = "E", "SIMPLE_TECH_EXIT_LIFECYCLE_BRANCH_INVALID"
        nxt = "STOP. Identity, causality, or non-interference failed."
    elif u_sup or p_sup:
        case, verdict = "A", "SIMPLE_TECH_EXIT_LIFECYCLE_BRANCH_SUPPORTED"
        nxt = (
            "STOP this run. Next run may precommit an EXIT mechanism on the supported branch only. "
            "Do not combine U and P. Do not implement that EXIT here."
        )
    elif u_mixed or p_mixed:
        case, verdict = "B", "SIMPLE_TECH_EXIT_LIFECYCLE_BRANCH_MIXED"
        nxt = "STOP. Do not add parameters on these 18 days."
    else:
        case, verdict = "C", "SIMPLE_TECH_EXIT_LIFECYCLE_BRANCH_NOT_SUPPORTED"
        nxt = "STOP. Reconsider EXIT improvement from current simple technical states. Do not return to K/threshold search."
    decomp = dict(summary.get("decomposition") or {})
    early_be = dict(decomp.get("EARLY_FAILURE") or {}).get("break_even_reached_rate")
    dip_be = dict(decomp.get("DIP_THEN_RECOVERY") or {}).get("break_even_reached_rate")
    q1 = bool(any(s != "U_NEVER_BREAK_EVEN" for s in u_sup))
    q1_partition = "U_NEVER_BREAK_EVEN" in u_sup
    q2 = bool(p_sup)
    q3 = bool(q1_partition or q1 or q2)
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "V27_FILL_IDENTITY_PARITY": bool(hash_ok and counts_ok),
        "K6_USED_FOR_SELECTION": False,
        "PNL_USED_FOR_SELECTION": False,
        "THRESHOLD_SEARCH": False,
        "COMBINATION_SEARCH": False,
        "GIVEBACK_SEARCH": False,
        "U_SUPPORTED": u_sup,
        "P_SUPPORTED": p_sup,
        "U_MIXED": u_mixed,
        "P_MIXED": p_mixed,
        "Q1_EARLY_VS_DIP_UNPROVEN": q1,
        "Q1_NEVER_BE_PARTITION": q1_partition,
        "Q2_PTF_VS_GOOD_DIP_PROVEN": q2,
        "Q3_SPLIT_CLEARER_THAN_V26_V29": q3,
        "EARLY_BE_RATE": early_be,
        "DIP_BE_RATE": dip_be,
        "EXIT_POLICY_CREATED": False,
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "NOT_V30": True,
    }
