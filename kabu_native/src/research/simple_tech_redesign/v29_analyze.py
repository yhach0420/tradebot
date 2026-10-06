"""V29 sequence frequency after first 3m EMA damage. No PnL selection. K6 is diagnostic only."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.v28_harvest import V28_CACHE, load_v28_day_cache
from research.simple_tech_redesign.v28_spec import spec_sha256_v28
from research.simple_tech_redesign.v29_spec import (
    ADDED_FILL_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    FAILURE_PATHS,
    FAMILY_B_SWING_AVAILABLE,
    FAMILY_B_SWING_REASON,
    MIN_CORE_PROT_N,
    MIN_DAY_PAIR_N,
    MIN_DAYS,
    MIN_FAIL_N,
    MIN_PROT_N,
    MIN_SYMBOLS,
    PATH_TYPES,
    PRIMARY_CANDIDATE_ORDER,
    PROTECTED_PATHS,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    SEQUENCE_IDS,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
    V27_EQUIVALENT,
)

FAILURE_LIKE = {
    "A_NO_RECLAIM",
    "A_RECLAIM_THEN_RELOSS",
    "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM",
    "B_PRICE_LOST_NO_RECLAIM",
    "B_PRICE_RECLAIM_THEN_RELOSS",
    "C_NO_RECLAIM_ATTEMPT",
    "C_RECLAIM_ATTEMPT_WITH_VOL_DET",
}


def _fills(rows: list[dict[str, Any]], role: Optional[str] = None) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        if role is not None and str(r.get("fill_role") or "") != role:
            continue
        out.append(r)
    return out


def _seq(r: dict[str, Any]) -> dict[str, Any]:
    return dict(r.get("terminal_seq") or {})


def _cls(r: dict[str, Any]) -> Optional[str]:
    p = str(r.get("path_type") or "")
    if p in PROTECTED_PATHS:
        return "PROTECTED"
    if p in FAILURE_PATHS:
        return "FAILURE"
    return "OTHER"


def _hit(r: dict[str, Any], seq_id: str) -> Optional[bool]:
    pack = _seq(r)
    if not pack.get("damage_onset"):
        return None
    if seq_id == "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM":
        if not pack.get("reclaimed"):
            return None
        return bool(pack.get("reloss_after_reclaim"))
    if seq_id.startswith("A_"):
        return str(pack.get("family_a") or "") == seq_id
    if seq_id.startswith("B_"):
        return str(pack.get("family_b") or "") == seq_id
    if seq_id.startswith("C_"):
        return str(pack.get("family_c") or "") == seq_id
    return None


def _rate(n: int, d: int) -> Optional[float]:
    if int(d) <= 0:
        return None
    return float(n) / float(d)


def _syms(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("symbol") or "").replace(".T", "") for r in rows}


def _days(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("date") or "") for r in rows}


def _day_sign(pop: list[dict[str, Any]], seq_id: str, *, failure_like: bool) -> dict[str, Any]:
    by: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"fail": [], "prot": []})
    for r in pop:
        v = _hit(r, seq_id)
        if v is None:
            continue
        cls = _cls(r)
        if cls == "FAILURE":
            by[str(r.get("date"))]["fail"].append(1 if v else 0)
        elif cls == "PROTECTED":
            by[str(r.get("date"))]["prot"].append(1 if v else 0)
    agree = disagree = usable = 0
    rows = []
    for d in ELIGIBLE_DAYS:
        g = by.get(d) or {"fail": [], "prot": []}
        if len(g["fail"]) < int(MIN_DAY_PAIR_N) or len(g["prot"]) < int(MIN_DAY_PAIR_N):
            rows.append({"date": d, "usable": False, "fail_rate": None, "prot_rate": None, "sign": None})
            continue
        usable += 1
        rf = float(sum(g["fail"])) / float(len(g["fail"]))
        rp = float(sum(g["prot"])) / float(len(g["prot"]))
        if failure_like:
            higher_fail = rf > rp
        else:
            higher_fail = rp < rf
        if rf == rp:
            sign = "TIE"
        elif (failure_like and rf > rp) or ((not failure_like) and rp > rf):
            agree += 1
            sign = "AGREE"
        else:
            disagree += 1
            sign = "DISAGREE"
        rows.append({"date": d, "usable": True, "fail_rate": rf, "prot_rate": rp, "sign": sign, "fail_higher": higher_fail})
    return {"usable_days": usable, "agree_days": agree, "disagree_days": disagree, "daily": rows}


def sequence_test(pop: list[dict[str, Any]], seq_id: str, *, failure_like: bool) -> dict[str, Any]:
    fail = [r for r in pop if _cls(r) == "FAILURE"]
    prot = [r for r in pop if _cls(r) == "PROTECTED"]

    def hits(rs: list[dict[str, Any]]) -> tuple[int, int]:
        vs = [v for v in (_hit(r, seq_id) for r in rs) if v is not None]
        return sum(1 for v in vs if v), len(vs)

    fh, fn = hits(fail)
    ph, pn = hits(prot)
    core_prot = [r for r in pop if str(r.get("fill_role") or "") == "CORE" and _cls(r) == "PROTECTED"]
    core_good = [r for r in pop if str(r.get("fill_role") or "") == "CORE" and str(r.get("path_type") or "") == "GOOD_CONTINUATION"]
    core_dip = [r for r in pop if str(r.get("fill_role") or "") == "CORE" and str(r.get("path_type") or "") == "DIP_THEN_RECOVERY"]
    added_fail = [r for r in pop if str(r.get("fill_role") or "") == "ADDED" and _cls(r) == "FAILURE"]
    added_prot = [r for r in pop if str(r.get("fill_role") or "") == "ADDED" and _cls(r) == "PROTECTED"]
    cph, cpn = hits(core_prot)
    cgh, cgn = hits(core_good)
    cdh, cdn = hits(core_dip)
    afh, afn = hits(added_fail)
    aph, apn = hits(added_prot)
    fr, pr, cpr = _rate(fh, fn), _rate(ph, pn), _rate(cph, cpn)
    cgr, cdr = _rate(cgh, cgn), _rate(cdh, cdn)
    path_hits: dict[str, Any] = {}
    for p in PATH_TYPES:
        rs = [r for r in pop if str(r.get("path_type") or "") == p]
        hh, nn = hits(rs)
        path_hits[p] = {"n": nn, "hit_n": hh, "hit_rate": _rate(hh, nn)}
    fail_hit_rows = [r for r in fail if _hit(r, seq_id) is True]
    hit_sym_counts = Counter(str(r.get("symbol") or "").replace(".T", "") for r in fail_hit_rows)
    top_sym, top_n = (hit_sym_counts.most_common(1)[0] if hit_sym_counts else ("", 0))
    top_share = _rate(int(top_n), len(fail_hit_rows))
    if failure_like:
        direction = fr is not None and pr is not None and float(fr) > float(pr)
        core_ok = fr is not None and cpr is not None and float(cpr) < float(fr)
        if cgn >= 2 and fr is not None and cgr is not None and float(cgr) > float(fr):
            core_ok = False
        added_ok = True
        if afn >= int(MIN_FAIL_N) and apn >= 4:
            afr, apr = _rate(afh, afn), _rate(aph, apn)
            added_ok = afr is not None and apr is not None and float(afr) > float(apr)
    else:
        direction = fr is not None and pr is not None and float(pr) > float(fr)
        core_ok = pr is not None and cpr is not None
        added_ok = True
        if afn >= int(MIN_FAIL_N) and apn >= 4:
            afr, apr = _rate(afh, afn), _rate(aph, apn)
            added_ok = afr is not None and apr is not None and float(apr) > float(afr)
    day = _day_sign(pop, seq_id, failure_like=failure_like)
    floors = (
        int(fn) >= int(MIN_FAIL_N)
        and int(pn) >= int(MIN_PROT_N)
        and int(cpn) >= int(MIN_CORE_PROT_N)
        and len(_days(fail)) >= int(MIN_DAYS)
        and len(_days(prot)) >= int(MIN_DAYS)
        and len(_syms(fail)) >= int(MIN_SYMBOLS)
        and len(_syms(prot)) >= int(MIN_SYMBOLS)
    )
    day_ok = int(day.get("usable_days") or 0) >= int(MIN_DAYS) and int(day.get("agree_days") or 0) > int(
        day.get("disagree_days") or 0
    )
    distinct = seq_id not in V27_EQUIVALENT
    supported = bool(floors and direction and core_ok and added_ok and day_ok and distinct)
    ratio = None
    if failure_like and pr is not None and fr is not None and float(pr) > 1e-12:
        ratio = float(fr) / float(pr)
    return {
        "seq_id": seq_id,
        "failure_like": bool(failure_like),
        "distinct_from_v27": bool(distinct),
        "SUPPORTED": supported,
        "floors_ok": floors,
        "direction_ok": bool(direction),
        "core_ok": bool(core_ok),
        "added_ok": bool(added_ok),
        "day_ok": bool(day_ok),
        "day": {"usable_days": day["usable_days"], "agree_days": day["agree_days"], "disagree_days": day["disagree_days"]},
        "daily": day.get("daily"),
        "protected_n": pn,
        "failure_n": fn,
        "sequence_hit_protected_n": ph,
        "sequence_hit_failure_n": fh,
        "protected_hit_rate": pr,
        "failure_hit_rate": fr,
        "bad_protected_rate_ratio": ratio,
        "core_protected_n": cpn,
        "core_protected_hit_n": cph,
        "core_protected_hit_rate": cpr,
        "core_good_n": cgn,
        "core_good_hit_n": cgh,
        "core_good_hit_rate": cgr,
        "core_dip_n": cdn,
        "core_dip_hit_n": cdh,
        "core_dip_hit_rate": cdr,
        "added_failure_n": afn,
        "added_failure_hit_n": afh,
        "added_failure_hit_rate": _rate(afh, afn),
        "added_protected_n": apn,
        "added_protected_hit_n": aph,
        "added_protected_hit_rate": _rate(aph, apn),
        "path_hits": path_hits,
        "fail_hit_n": len(fail_hit_rows),
        "fail_hit_symbol_n": len(hit_sym_counts),
        "top_fail_hit_symbol": top_sym or None,
        "top_fail_hit_share": top_share,
        "AM_N": fn + pn,
        "PM_N": 0,
        "fail_days": len(_days(fail)),
        "prot_days": len(_days(prot)),
        "fail_symbols": len(_syms(fail)),
        "prot_symbols": len(_syms(prot)),
        "symbol_coverage": len(_syms(pop)),
    }


def _v28_tech_keys() -> set[tuple[str, str, float]]:
    sha = spec_sha256_v28()
    keys: set[tuple[str, str, float]] = set()
    for day in ELIGIBLE_DAYS:
        body = load_v28_day_cache(V28_CACHE / f"day_{day}.json", sha)
        for r in list(body.get("rows") or []):
            if str((r.get("treatment_exit") or {}).get("reason") or "") != "TECHNICAL_EXIT":
                continue
            keys.add((str(r.get("date") or ""), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)))
    return keys


def _key(r: dict[str, Any]) -> tuple[str, str, float]:
    return (str(r.get("date") or ""), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = _fills(rows)
    core = _fills(rows, "CORE")
    added = _fills(rows, "ADDED")
    damage = [r for r in fills if _seq(r).get("damage_onset")]
    tests = [sequence_test(damage, sid, failure_like=sid in FAILURE_LIKE) for sid in SEQUENCE_IDS]
    supported = [t for t in tests if t.get("SUPPORTED")]
    primary = None
    for sid in PRIMARY_CANDIDATE_ORDER:
        hits = [t for t in supported if t["seq_id"] == sid]
        if hits:
            primary = {k: hits[0][k] for k in hits[0] if k != "daily"}
            break
    mixed = [
        t["seq_id"]
        for t in tests
        if t["seq_id"] in PRIMARY_CANDIDATE_ORDER
        and t.get("direction_ok")
        and t.get("distinct_from_v27")
        and not t.get("SUPPORTED")
    ]
    def _trade_pack(r: dict[str, Any]) -> dict[str, Any]:
        pack = _seq(r)
        return {
            "date": r.get("date"),
            "symbol": str(r.get("symbol") or "").replace(".T", ""),
            "t0": r.get("t0"),
            "fill_role": r.get("fill_role"),
            "path_type": r.get("path_type"),
            "class": _cls(r),
            "family_a": pack.get("family_a"),
            "family_b": pack.get("family_b"),
            "family_c": pack.get("family_c"),
            "reclaimed": pack.get("reclaimed"),
            "reloss_after_reclaim": pack.get("reloss_after_reclaim"),
            "k6_reached": pack.get("k6_reached"),
            "k6_recovered_before_trigger_n": pack.get("k6_recovered_before_trigger_n"),
            "damage_onset": pack.get("damage_onset"),
            "A_RECLAIM_THEN_RELOSS": _hit(r, "A_RECLAIM_THEN_RELOSS"),
            "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM": _hit(r, "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM"),
            "B_PRICE_RECLAIM_THEN_RELOSS": _hit(r, "B_PRICE_RECLAIM_THEN_RELOSS"),
            "C_RECLAIM_ATTEMPT_WITH_VOL_DET": _hit(r, "C_RECLAIM_ATTEMPT_WITH_VOL_DET"),
        }

    v28_rows = [r for r in added if _key(r) in _v28_tech_keys()]
    v28_audit: dict[str, Any] = {"n": len(v28_rows), "by_path": {}, "trades": [_trade_pack(r) for r in v28_rows]}
    for p in PATH_TYPES:
        grp = [r for r in v28_rows if str(r.get("path_type") or "") == p]
        v28_audit["by_path"][p] = {
            "n": len(grp),
            "family_a": {
                k: sum(1 for r in grp if str(_seq(r).get("family_a") or "") == k)
                for k in ("A_NO_RECLAIM", "A_RECLAIM_THEN_MAINTAIN", "A_RECLAIM_THEN_RELOSS")
            },
            "seq_hits": {sid: sum(1 for r in grp if _hit(r, sid) is True) for sid in PRIMARY_CANDIDATE_ORDER},
        }
    recov = [
        r
        for r in added
        if int(_seq(r).get("k6_recovered_before_trigger_n") or 0) > 0 and not _seq(r).get("k6_reached")
    ]
    events = []
    for r in damage:
        for ev in list(_seq(r).get("events") or []):
            events.append(
                {
                    "date": r.get("date"),
                    "symbol": str(r.get("symbol") or "").replace(".T", ""),
                    "t0": r.get("t0"),
                    "fill_role": r.get("fill_role"),
                    "path_type": r.get("path_type"),
                    **ev,
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
        "DAMAGE_ONSET_N": len(damage),
        "NO_DAMAGE_N": len(fills) - len(damage),
        "K6_USED_FOR_SELECTION": False,
        "PNL_USED_FOR_SELECTION": False,
        "FAMILY_B_SWING_AVAILABLE": bool(FAMILY_B_SWING_AVAILABLE),
        "FAMILY_B_SWING_REASON": FAMILY_B_SWING_REASON,
        "FAMILY_A_DIST": {
            k: sum(1 for r in damage if str(_seq(r).get("family_a") or "") == k)
            for k in ("A_NO_RECLAIM", "A_RECLAIM_THEN_MAINTAIN", "A_RECLAIM_THEN_RELOSS")
        },
        "FAMILY_B_DIST": {
            k: sum(1 for r in damage if str(_seq(r).get("family_b") or "") == k)
            for k in ("B_PRICE_NEVER_LOST", "B_PRICE_LOST_NO_RECLAIM", "B_PRICE_RECLAIM_THEN_MAINTAIN", "B_PRICE_RECLAIM_THEN_RELOSS")
        },
        "FAMILY_C_DIST": {
            k: sum(1 for r in damage if str(_seq(r).get("family_c") or "") == k)
            for k in ("C_NO_RECLAIM_ATTEMPT", "C_RECLAIM_ATTEMPT_WITH_VOL_DET", "C_RECLAIM_ATTEMPT_WITHOUT_VOL_DET")
        },
        "tests": tests,
        "SUPPORTED_SEQUENCES": [t["seq_id"] for t in supported],
        "PRIMARY_EXIT_SEQUENCE_MECHANISM": primary,
        "MIXED_SEQUENCES": mixed,
        "V28_TECH_EXIT_AUDIT": v28_audit,
        "K6_RECOVERED_BEFORE_AUDIT": {
            "n": len(recov),
            "family_a": {
                k: sum(1 for r in recov if str(_seq(r).get("family_a") or "") == k)
                for k in ("A_NO_RECLAIM", "A_RECLAIM_THEN_MAINTAIN", "A_RECLAIM_THEN_RELOSS")
            },
            "by_class": {
                "PROTECTED": sum(1 for r in recov if _cls(r) == "PROTECTED"),
                "FAILURE": sum(1 for r in recov if _cls(r) == "FAILURE"),
                "OTHER": sum(1 for r in recov if _cls(r) == "OTHER"),
            },
            "seq_hits": {sid: sum(1 for r in recov if _hit(r, sid) is True) for sid in PRIMARY_CANDIDATE_ORDER},
            "trades": [_trade_pack(r) for r in recov],
        },
        "K6_REACHED_N": sum(1 for r in fills if _seq(r).get("k6_reached")),
        "event_rows": events,
        "CORE_DAMAGE_N": sum(1 for r in core if _seq(r).get("damage_onset")),
        "ADDED_DAMAGE_N": sum(1 for r in added if _seq(r).get("damage_onset")),
        "path_damage_n": {p: sum(1 for r in damage if str(r.get("path_type") or "") == p) for p in PATH_TYPES},
    }


def decide(
    summary: dict[str, Any],
    *,
    signal_parity: bool,
    fill_identity: bool,
    leak_ok: bool,
    ni_ok: bool,
) -> dict[str, Any]:
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
    primary = summary.get("PRIMARY_EXIT_SEQUENCE_MECHANISM")
    mixed = list(summary.get("MIXED_SEQUENCES") or [])
    if not integrity:
        case, verdict = "E", "SIMPLE_TECH_V29_INVALID"
        next_step = "STOP. Identity, causality, or non-interference failed."
    elif primary:
        case, verdict = "A", "SIMPLE_TECH_V29_TERMINAL_FAILURE_SEQUENCE_FOUND"
        next_step = (
            "V30: convert PRIMARY_EXIT_SEQUENCE_MECHANISM into one precommitted causal EXIT state machine. "
            "No combination search. No PnL ranking. Do not retune K."
        )
    elif mixed:
        case, verdict = "B", "SIMPLE_TECH_V29_TERMINAL_FAILURE_SEQUENCE_MIXED"
        next_step = "STOP. Do not micro-adjust sequences on these 18 days."
    else:
        case, verdict = "C", "SIMPLE_TECH_V29_TERMINAL_FAILURE_SEQUENCE_NOT_FOUND"
        next_step = (
            "STOP. Simple technical states cannot safely separate temporary DIP from terminal failure. "
            "Do not return to EMA threshold search."
        )
    sid = str((primary or {}).get("seq_id") or "") if isinstance(primary, dict) else ""
    if sid in {"A_RECLAIM_THEN_RELOSS", "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM"}:
        vs_v27 = (
            "V27 NON_RECOVERY asked whether 3m EMA structure ever recovered after damage. "
            "V29 reclaim-then-reloss asks whether a completed-bar reclaim was followed by a completed-bar reloss. "
            "A trade that recovers once then collapses is a V27 recovery and a V29 failure-quality sequence."
        )
    elif sid == "B_PRICE_RECLAIM_THEN_RELOSS":
        vs_v27 = (
            "V27 did not score Close<EMA9 reclaim/reloss after EMA damage. "
            "Family B uses only frozen V26 C_PRICE_STRUCTURE_LOSS transitions. HH/HL/LH/LL were not invented."
        )
    elif sid == "C_RECLAIM_ATTEMPT_WITH_VOL_DET":
        vs_v27 = (
            "V26 volume deterioration as a standalone EXIT failed. Family C is conditional: "
            "after damage, a Close>EMA9 reclaim attempt with versus without V26 F_VOLUME_DETERIORATION."
        )
    else:
        vs_v27 = (
            "No PRIMARY certified. A_NO_RECLAIM is the V27 simple non-recovery equivalent and is excluded "
            "from PRIMARY_CANDIDATE_ORDER."
        )
    v28 = dict(summary.get("V28_TECH_EXIT_AUDIT") or {})
    v28_by = dict(v28.get("by_path") or {})
    recov = dict(summary.get("K6_RECOVERED_BEFORE_AUDIT") or {})
    core_ok = bool((primary or {}).get("core_ok")) if isinstance(primary, dict) else None
    day = dict((primary or {}).get("day") or {}) if isinstance(primary, dict) else {}
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "V27_FILL_IDENTITY_PARITY": bool(hash_ok and counts_ok),
        "K6_USED_FOR_SELECTION": False,
        "PNL_USED_FOR_SELECTION": False,
        "THRESHOLD_SEARCH": False,
        "COMBINATION_SEARCH": False,
        "PRIMARY_EXIT_SEQUENCE_MECHANISM": primary,
        "SUPPORTED_SEQUENCES": summary.get("SUPPORTED_SEQUENCES"),
        "MIXED_SEQUENCES": mixed,
        "VS_V27_SIMPLE_RECOVERY": vs_v27,
        "CORE_DIRECTION_OK": core_ok,
        "DAY_AGREEMENT": day,
        "V28_TECH_EXIT_N": v28.get("n"),
        "V28_BY_PATH_N": {p: dict(v28_by.get(p) or {}).get("n") for p in PATH_TYPES},
        "K6_RECOVERED_BEFORE_TRADE_N": recov.get("n"),
        "EXIT_POLICY_CREATED": False,
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
    }
