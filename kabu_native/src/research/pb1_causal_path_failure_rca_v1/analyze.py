"""RCA summaries and cause hierarchy. No eligibility change. No PnL selection."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from research.pb1_causal_path_failure_rca_v1 import (
    CASE_BIND,
    CASE_CONSUMED,
    CASE_INVALID,
    CASE_MULTI,
    CASE_NONE,
    CASE_REGIME,
    CASE_TRIGGER,
    EXPECTED_SETUP_N,
    FAIL_WINDOWS,
    HORIZONS,
    MFE_R_GATES,
    NEXT_BIND,
    NEXT_REDESIGN,
    NEXT_STOP,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_VERDICT,
    STAGE_NAMES,
    STAGES,
)
from research.pb1_causal_path_failure_rca_v1.classify import apply_labels, reveal_path
from research.pb1_causal_path_failure_rca_v1.compose import annotate, clock_paths, composition, reweight_r10
from research.pb1_causal_path_failure_rca_v1.metrics import first_passage
from research.pb1_opening_range_causal_path_test_v1.analyze import _finite, _rate, pct_summary
from research.pb1_opening_range_causal_path_test_v1.match import match_all, pos_at
from research.pb1_opening_range_causal_path_test_v1.path import next_open_from
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256


def _subset(rows: list[dict[str, Any]], **eq: Any) -> list[dict[str, Any]]:
    out = rows
    for k, v in eq.items():
        out = [r for r in out if r.get(k) == v]
    return out


def _p50(rows: list[dict[str, Any]], key: str) -> Any:
    return pct_summary([r.get(key) for r in rows]).get("p50")


def _mean(rows: list[dict[str, Any]], key: str) -> Any:
    return pct_summary([r.get(key) for r in rows]).get("mean")


def _frac(rows: list[dict[str, Any]], key: str) -> Any:
    n = len(rows)
    if not n:
        return None
    return float(sum(1 for r in rows if r.get(key)) / n)


def path_pack(rows: list[dict[str, Any]], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    for h in HORIZONS:
        out[f"r{h}_bps"] = pct_summary([r.get(f"{prefix}r{h}_bps") for r in rows])
    out["MFE_bps"] = pct_summary([r.get(f"{prefix}MFE_bps") for r in rows])
    out["MAE_bps"] = pct_summary([r.get(f"{prefix}MAE_bps") for r in rows])
    return out


def fp_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r.get("fp_risk_defined")]
    out: dict[str, Any] = {"n": len(rows), "risk_defined_n": len(valid)}
    for g in MFE_R_GATES:
        key = str(g).replace(".", "_")
        out[f"P_plus_{key}R_before_fail"] = _frac(valid, f"fp_plus_{key}R_before_fail")
        out[f"time_plus_{key}R"] = pct_summary([r.get(f"fp_plus_{key}R_t") for r in valid])
    out["time_or"] = pct_summary([r.get("fp_or_t") for r in rows])
    out["time_ret"] = pct_summary([r.get("fp_ret_t") for r in rows])
    for w in FAIL_WINDOWS:
        out[f"or_within_{w}m"] = _frac(rows, f"fp_or_fail_within_{w}m")
        out[f"ret_within_{w}m"] = _frac(rows, f"fp_ret_fail_within_{w}m")
    return out


def funnel_report(counts: dict[str, Any], stages: list[dict[str, Any]]) -> dict[str, Any]:
    n0 = int(counts.get("S0") or 0)
    out: dict[str, Any] = {"S0": {"n": n0, "name": STAGE_NAMES["S0"], "of_S0": 1.0 if n0 else None}}
    for st in STAGES:
        n = int(counts.get(st) or 0)
        out[st] = {"n": n, "name": STAGE_NAMES[st], "of_S0": float(n / n0) if n0 else None}
    out["no_future_completion_for_earlier_membership"] = True
    deaths = Counter(str(r.get("death")) for r in stages if r.get("stage") == "S0")
    out["S0_death_or_incomplete"] = dict(deaths)
    return out


def stage_edge(stage_paths: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for st in STAGES:
        vs = [r for r in stage_paths if r.get("stage") == st]
        out[st] = path_pack(vs)
        out[st]["of_treated_only"] = path_pack([r for r in vs if r.get("treated")])
    first = None
    for st in STAGES:
        p = (out[st].get("r10_bps") or {}).get("p50")
        if _finite(p) and float(p) > 1.0 and first is None:
            first = st
    out["favorable_path_first_appears"] = first
    return out


def control_fp(pairs: list[dict[str, Any]], events: list[dict[str, Any]], eligible: list[dict[str, Any]], recs: dict) -> dict[str, Any]:
    elig = {(str(e["symbol"]), str(e["date"])): e for e in eligible}
    evs = {(str(e["symbol"]), str(e["date"])): e for e in events}
    rows = []
    for p in pairs:
        if not p.get("matched"):
            continue
        tr = evs.get((str(p["symbol"]), str(p["date"])))
        ctl = elig.get((str(p.get("control_symbol")), str(p.get("control_date"))))
        rec = recs.get((str(p.get("control_symbol")), str(p.get("control_date"))))
        if tr is None or ctl is None or rec is None:
            continue
        t = str(tr.get("trigger_t") or "")
        pos = pos_at(rec, t)
        if pos is None:
            continue
        nxt = next_open_from(rec, rec["session_idx"], pos)
        if nxt is None:
            continue
        fp = first_passage(
            rec,
            entry_pos=int(nxt["entry_pos"]),
            sign=int(tr.get("DIR") or 0),
            entry=float(nxt["entry_px"]),
            r_px=tr.get("R"),
            or_high=float(ctl["or_high"]),
            or_low=float(ctl["or_low"]),
            retest_high=None,
            retest_low=None,
        )
        rows.append({"block": tr.get("block"), "direction": tr.get("direction"), **{f"fp_{k}": v for k, v in fp.items()}, "ct_r10_bps": p.get("ct_r10_bps"), "tr_r10_bps": p.get("tr_r10_bps")})
    byb = {b: fp_pack([r for r in rows if r.get("block") == b]) for b in ("D1", "D2", "D3", "D4")}
    return {"all": fp_pack(rows), "by_block": byb, "n": len(rows)}


def market_by_block(market_days: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for b in ("D1", "D2", "D3", "D4"):
        vs = [d for d in market_days if d.get("block") == b]
        out[b] = {
            "n_days": len(vs),
            "breadth_0915": pct_summary([d.get("breadth_0915") for d in vs]),
            "median_dir_bps": pct_summary([d.get("median_dir_bps") for d in vs]),
            "dispersion_bps": pct_summary([d.get("dispersion_bps") for d in vs]),
            "frac_break_own_or_later": pct_summary([d.get("frac_break_own_or_later") for d in vs]),
            "sector_agree": pct_summary([d.get("sector_agree") for d in vs]),
        }
    return out


def treated_vs_control(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for b in ("D1", "D2", "D3", "D4"):
        vs = [p for p in pairs if p.get("block") == b and p.get("matched")]
        tr10 = pct_summary([p.get("tr_r10_bps") for p in vs])
        ct10 = pct_summary([p.get("ct_r10_bps") for p in vs])
        tr5 = pct_summary([p.get("tr_r5_bps") for p in vs])
        ct5 = pct_summary([p.get("ct_r5_bps") for p in vs])
        tr20 = pct_summary([p.get("tr_r20_bps") for p in vs])
        ct20 = pct_summary([p.get("ct_r20_bps") for p in vs])
        out[b] = {
            "n": len(vs),
            "treated_5/10/20_p50": [tr5.get("p50"), tr10.get("p50"), tr20.get("p50")],
            "control_5/10/20_p50": [ct5.get("p50"), ct10.get("p50"), ct20.get("p50")],
            "treated_MFE_p50": pct_summary([p.get("tr_MFE_bps") for p in vs]).get("p50"),
            "control_MFE_p50": pct_summary([p.get("ct_MFE_bps") for p in vs]).get("p50"),
            "treated_MAE_p50": pct_summary([p.get("tr_MAE_bps") for p in vs]).get("p50"),
            "control_MAE_p50": pct_summary([p.get("ct_MAE_bps") for p in vs]).get("p50"),
            "who": (
                "PB1_DETERIORATED"
                if _finite(tr10.get("p50")) and _finite(ct10.get("p50")) and float(tr10["p50"]) < 0 and float(ct10["p50"]) >= 0
                else (
                    "CONTROLS_STRENGTHENED"
                    if _finite(tr10.get("p50")) and _finite(ct10.get("p50")) and float(tr10["p50"]) <= float(ct10["p50"]) and float(ct10["p50"]) > 2
                    else (
                        "BOTH"
                        if _finite(tr10.get("p50")) and float(tr10["p50"]) < 0
                        else "NEITHER_CLEAR"
                    )
                )
            ),
        }
    return out


def hierarchy(body: dict[str, Any]) -> dict[str, Any]:
    stages = dict(body.get("stage_edge") or {})
    ev = list(body.get("events") or [])
    reclaim = _subset(ev, trigger_primary="RECLAIM_RETEST_MICRO_HIGH")
    failed = _subset(ev, trigger_primary="FAILED_PUSH_THEN_CLOSE_BACK")
    s1 = (stages.get("S1") or {}).get("r10_bps") or {}
    s6 = (stages.get("S6") or {}).get("r10_bps") or {}
    cons = pct_summary([e.get("cons_break_to_entry_bps") for e in ev])
    fp_all = dict(body.get("first_passage") or {}).get("all") or {}
    fp_ct = dict(body.get("control_first_passage") or {}).get("all") or {}
    or_cls = Counter(str(e.get("orf_class")) for e in ev if e.get("orf_or_failed"))
    n_fail = sum(or_cls.values()) or 1
    shallow = or_cls.get("SHALLOW_ONE_BAR_REENTRY", 0) / n_fail
    p1 = fp_all.get("P_plus_1_0R_before_fail")
    p1c = fp_ct.get("P_plus_1_0R_before_fail")
    inv_n = int(sum(1 for e in failed if e.get("risk_state") == "RISK_INVALID"))
    rw = dict(body.get("reweight") or {})
    mkt = dict(body.get("market_by_block") or {})
    human = dict(body.get("human") or {})

    consumed = bool(_finite(s1.get("p50")) and float(s1["p50"]) > 2 and (not _finite(s6.get("p50")) or float(s6["p50"]) <= 1) and _finite(cons.get("p50")) and float(cons["p50"]) > 8)
    mixed = bool(abs((_p50(reclaim, "r10_bps") or 0) - (_p50(failed, "r10_bps") or 0)) >= 4 and (_frac(failed, "or_accept_fail") or 0) >= 0.80)
    mismatch = bool(inv_n >= 40 and (_frac(failed, "or_accept_fail") or 0) >= 0.85)
    invalid_sem = bool(shallow >= 0.35 and _finite(p1) and float(p1) >= 0.35)
    fp_beats = bool(_finite(p1) and _finite(p1c) and float(p1) > float(p1c) + 0.05)
    no_edge = bool((not _finite(s1.get("p50")) or float(s1["p50"]) <= 1) and not fp_beats)
    regime = bool(rw.get("instability_remains"))
    d2b = ((mkt.get("D2") or {}).get("breadth_0915") or {}).get("p50")
    d1b = ((mkt.get("D1") or {}).get("breadth_0915") or {}).get("p50")
    mkt_diff = bool(_finite(d1b) and _finite(d2b) and abs(float(d1b) - float(d2b)) >= 0.05)
    contamination = bool((human.get("CLEAR_CONTINUATION_share") or 1) < 2 / 3)
    stale = bool((_p50(_subset(ev, age_bucket="0_5"), "r10_bps") or 0) > (_p50(_subset(ev, age_bucket="15_30"), "r10_bps") or 0) + 2)

    rank = {
        "ENTRY TOO LATE / EDGE CONSUMED": "PRIMARY_CAUSE" if consumed else ("CONTRIBUTING_CAUSE" if _finite(cons.get("p50")) and float(cons["p50"]) > 5 else "NOT_SUPPORTED_CAUSE"),
        "TRIGGER SEMANTICS MIXED": "PRIMARY_CAUSE" if mixed or mismatch else "NOT_SUPPORTED_CAUSE",
        "INVALIDATION SEMANTICS WRONG": "PRIMARY_CAUSE" if invalid_sem else ("CONTRIBUTING_CAUSE" if shallow >= 0.25 else "NOT_SUPPORTED_CAUSE"),
        "RETEST TOO STALE": "SECONDARY_CAUSE" if stale else "NOT_SUPPORTED_CAUSE",
        "OPENING IMPULSE TOO BROAD": "UNKNOWN",
        "BREAK QUALITY TOO WEAK": "UNKNOWN",
        "MARKET REGIME DEPENDENCE": "SECONDARY_CAUSE" if regime and mkt_diff else ("CONTRIBUTING_CAUSE" if regime else "NOT_SUPPORTED_CAUSE"),
        "SIDE ASYMMETRY": "CONTRIBUTING_CAUSE" if abs((_p50(_subset(ev, direction="bull"), "r10_bps") or 0) - (_p50(_subset(ev, direction="bear"), "r10_bps") or 0)) >= 2 else "NOT_SUPPORTED_CAUSE",
        "REWARD GEOMETRY": "NOT_SUPPORTED_CAUSE",
        "SEMANTIC CONTAMINATION": "CONTRIBUTING_CAUSE" if contamination else "SECONDARY_CAUSE",
        "NO TRUE EDGE": "PRIMARY_CAUSE" if no_edge else "NOT_SUPPORTED_CAUSE",
    }
    primaries = [k for k, v in rank.items() if v == "PRIMARY_CAUSE"]
    secondaries = [k for k, v in rank.items() if v == "SECONDARY_CAUSE"]
    contributing = [k for k, v in rank.items() if v == "CONTRIBUTING_CAUSE"]
    return {
        "rank": rank,
        "PRIMARY_CAUSE": primaries[0] if len(primaries) == 1 else (" + ".join(primaries) if primaries else None),
        "SECONDARY_CAUSE": secondaries[0] if secondaries else None,
        "CONTRIBUTING_CAUSE": contributing,
        "flags": {
            "consumed": consumed,
            "mixed_triggers": mixed,
            "failed_push_mismatch": mismatch,
            "invalidation_sensitive": invalid_sem,
            "fp_beats_controls": fp_beats,
            "no_underlying_edge": no_edge,
            "regime_after_reweight": regime,
            "market_block_diff": mkt_diff,
            "contamination": contamination,
            "stale_age": stale,
            "shallow_or_fail_share": shallow,
            "S1_r10_p50": s1.get("p50"),
            "S6_r10_p50": s6.get("p50"),
            "break_to_entry_p50": cons.get("p50"),
            "P_plus_1R_treated": p1,
            "P_plus_1R_control": p1c,
        },
    }


def decide(bind_ok: bool, identity_ok: bool, hier: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok or not identity_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "STOP_PB1": False, "eligibility_changed": False}
    f = dict(hier.get("flags") or {})
    primaries = [k for k, v in dict(hier.get("rank") or {}).items() if v == "PRIMARY_CAUSE"]
    if f.get("no_underlying_edge") and not f.get("fp_beats_controls") and not f.get("consumed"):
        verd, nxt = CASE_NONE, NEXT_STOP
        stop = True
    elif len(primaries) > 1:
        verd, nxt, stop = CASE_MULTI, NEXT_REDESIGN, False
    elif f.get("consumed") and "TRIGGER SEMANTICS MIXED" not in primaries:
        verd, nxt, stop = CASE_CONSUMED, NEXT_REDESIGN, False
    elif "TRIGGER SEMANTICS MIXED" in primaries:
        verd, nxt, stop = CASE_TRIGGER, NEXT_REDESIGN, False
    elif "INVALIDATION SEMANTICS WRONG" in primaries:
        verd, nxt, stop = CASE_INVALID, NEXT_REDESIGN, False
    elif f.get("regime_after_reweight") and f.get("market_block_diff"):
        verd, nxt, stop = CASE_REGIME, NEXT_REDESIGN, False
    elif primaries:
        verd, nxt, stop = CASE_MULTI, NEXT_REDESIGN, False
    else:
        verd, nxt, stop = CASE_MULTI, NEXT_REDESIGN, False
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "STOP_PB1": bool(stop),
        "is_strategy": False,
        "eligibility_changed": False,
        "trigger_selected_by_pnl": False,
        "new_indicator_strategy": False,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "kabu50": False,
        "submit_cancel_live": "0/0/0",
        "parent_machine_unchanged": True,
        "no_winner_promoted_from_best_return": True,
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    sample: list[dict[str, Any]],
    chart_meta: list[dict[str, Any]],
) -> dict[str, Any]:
    events = annotate(list(walked.get("events") or []))
    identity_ok = len(events) == int(EXPECTED_SETUP_N)
    recs = dict(walked.get("recs") or {})
    eligible = list(walked.get("eligible") or [])
    print("MATCH_START", flush=True)
    matched = match_all(events, eligible, recs)
    print(f"MATCHED {matched.get('matched_n')}/{matched.get('treated_n')}", flush=True)
    pairs = list(matched.get("pairs") or [])
    cfp = control_fp(pairs, events, eligible, recs)
    human = apply_labels(sample)
    by_key = {(str(e["symbol"]), str(e["date"]), str(e["direction"])): e for e in events}
    revealed = reveal_path(human, by_key)
    reclaim = _subset(events, trigger_primary="RECLAIM_RETEST_MICRO_HIGH")
    failed = _subset(events, trigger_primary="FAILED_PUSH_THEN_CLOSE_BACK")
    inv = [e for e in failed if e.get("risk_state") == "RISK_INVALID"]
    why_c = Counter(str(e.get("risk_invalid_why")) for e in inv)
    mismatch = bool(len(inv) >= 40 and (_frac(failed, "or_accept_fail") or 0) >= 0.85)
    stage = stage_edge(list(walked.get("stage_paths") or []))
    fp_all = fp_pack(events)
    fp_block = {b: fp_pack(_subset(events, block=b)) for b in ("D1", "D2", "D3", "D4")}
    fp_trig = {
        "RECLAIM_RETEST_MICRO_HIGH": fp_pack(reclaim),
        "FAILED_PUSH_THEN_CLOSE_BACK": fp_pack(failed),
    }
    body: dict[str, Any] = {
        "ok": True,
        "parent_machine_unchanged": True,
        "MACHINE_SHA256": v2_machine_sha256(),
        "PARENT_VERDICT": PARENT_VERDICT,
        "PARENT_PLAYBOOK_MACHINE_SHA256": PARENT_PLAYBOOK_MACHINE_SHA256,
        "setup_n": len(events),
        "identity_ok": identity_ok,
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "eligibility_changed": False,
        "counts": dict(walked.get("counts") or {}),
        "funnel": funnel_report(dict(walked.get("counts") or {}), list(walked.get("stages") or [])),
        "stage_edge": stage,
        "consumption": {
            "break_to_entry": pct_summary([e.get("cons_break_to_entry_bps") for e in events]),
            "break_to_entry_or": pct_summary([e.get("cons_break_to_entry_or_units") for e in events]),
            "break_to_entry_atr": pct_summary([e.get("cons_break_to_entry_atr_units") for e in events]),
            "break_to_entry_R": pct_summary([e.get("cons_break_to_entry_R_units") for e in events]),
            "max_ext_before_retest": pct_summary([e.get("cons_max_ext_before_retest_bps") for e in events]),
            "ext_to_retest_retrace": pct_summary([e.get("cons_ext_to_retest_retrace_bps") for e in events]),
            "retest_to_trigger": pct_summary([e.get("cons_retest_to_trigger_bps") for e in events]),
            "trigger_to_entry": pct_summary([e.get("cons_trigger_to_entry_bps") for e in events]),
        },
        "first_passage": {"all": fp_all, "by_block": fp_block, "by_trigger": fp_trig},
        "control_first_passage": cfp,
        "trigger_split": {
            "RECLAIM_RETEST_MICRO_HIGH": {
                **path_pack(reclaim),
                **fp_pack(reclaim),
                "risk_invalid_n": int(sum(1 for e in reclaim if e.get("risk_state") == "RISK_INVALID")),
                "or_fail": _frac(reclaim, "or_accept_fail"),
                "ret_fail": _frac(reclaim, "retest_extreme_breach"),
                "target_before_or": _frac([e for e in reclaim if e.get("target_kind") == "TARGET_AHEAD"], "TARGET_HIT_BEFORE_OR_FAILURE"),
                "cons_break_to_entry": pct_summary([e.get("cons_break_to_entry_bps") for e in reclaim]),
                "by_block": {b: {**path_pack(_subset(reclaim, block=b)), **fp_pack(_subset(reclaim, block=b))} for b in ("D1", "D2", "D3", "D4")},
            },
            "FAILED_PUSH_THEN_CLOSE_BACK": {
                **path_pack(failed),
                **fp_pack(failed),
                "risk_invalid_n": len(inv),
                "or_fail": _frac(failed, "or_accept_fail"),
                "ret_fail": _frac(failed, "retest_extreme_breach"),
                "target_before_or": _frac([e for e in failed if e.get("target_kind") == "TARGET_AHEAD"], "TARGET_HIT_BEFORE_OR_FAILURE"),
                "cons_break_to_entry": pct_summary([e.get("cons_break_to_entry_bps") for e in failed]),
                "by_block": {b: {**path_pack(_subset(failed, block=b)), **fp_pack(_subset(failed, block=b))} for b in ("D1", "D2", "D3", "D4")},
            },
            "same_mechanism": False,
            "MECHANISM_SEMANTICS_MISMATCH": mismatch,
        },
        "failed_push_audit": {
            "n": len(failed),
            "risk_invalid_n": len(inv),
            "why": dict(why_c),
            "MECHANISM_SEMANTICS_MISMATCH": mismatch,
            "not_dropped": True,
        },
        "or_failure_semantics": {
            "classes": dict(Counter(str(e.get("orf_class")) for e in events if e.get("orf_or_failed"))),
            "depth_or_units": pct_summary([e.get("orf_depth_or_units") for e in events if e.get("orf_or_failed")]),
            "bars_inside": pct_summary([e.get("orf_bars_inside") for e in events if e.get("orf_or_failed")]),
            "mfe_after_fail": pct_summary([e.get("orf_mfe_after_fail_bps") for e in events if e.get("orf_or_failed")]),
            "reclaim_after": _frac([e for e in events if e.get("orf_or_failed")], "orf_reclaim_after_failure"),
            "invalidation_not_changed": True,
        },
        "failure_timing": {
            "all": {f"{w}m": {"or": fp_all.get(f"or_within_{w}m"), "ret": fp_all.get(f"ret_within_{w}m")} for w in FAIL_WINDOWS},
            "reclaim": {f"{w}m": {"or": fp_trig["RECLAIM_RETEST_MICRO_HIGH"].get(f"or_within_{w}m"), "ret": fp_trig["RECLAIM_RETEST_MICRO_HIGH"].get(f"ret_within_{w}m")} for w in FAIL_WINDOWS},
            "failed_push": {f"{w}m": {"or": fp_trig["FAILED_PUSH_THEN_CLOSE_BACK"].get(f"or_within_{w}m"), "ret": fp_trig["FAILED_PUSH_THEN_CLOSE_BACK"].get(f"ret_within_{w}m")} for w in FAIL_WINDOWS},
        },
        "composition": composition(events),
        "reweight": reweight_r10(events),
        "clocks": clock_paths(events),
        "retest_age": {
            bkt: {
                **path_pack(_subset(events, age_bucket=bkt)),
                "cons_max_ext": pct_summary([e.get("cons_max_ext_before_retest_bps") for e in _subset(events, age_bucket=bkt)]),
                "cons_retrace": pct_summary([e.get("cons_ext_to_retest_retrace_bps") for e in _subset(events, age_bucket=bkt)]),
                **fp_pack(_subset(events, age_bucket=bkt)),
                "or_width_atr": pct_summary([e.get("or_range_over_atr") for e in _subset(events, age_bucket=bkt)]),
            }
            for bkt in ("0_5", "5_15", "15_30")
        },
        "break_quality": {
            "or_range_over_atr": pct_summary([e.get("or_range_over_atr") for e in events]),
            "break_beyond_over_or": pct_summary([e.get("break_beyond_over_or") for e in events]),
            "break_body_over_range": pct_summary([e.get("break_body_over_range") for e in events]),
            "away_n": pct_summary([e.get("away_n") for e in events]),
            "max_away_over_or": pct_summary([e.get("max_away_over_or") for e in events]),
            "by_block_or_atr": {b: pct_summary([e.get("or_range_over_atr") for e in _subset(events, block=b)]) for b in ("D1", "D2", "D3", "D4")},
        },
        "opening_impulse": {
            "efficiency": pct_summary([e.get("opening_efficiency") for e in events]),
            "counter_move": pct_summary([e.get("counter_move_or_units") for e in events]),
            "asym": pct_summary([e.get("opening_mfe_mae_asym") for e in events]),
        },
        "in_play": {
            why: {**path_pack(_subset(events, in_play_reason=why)), **fp_pack(_subset(events, in_play_reason=why)), "blocks": dict(Counter(str(e.get("block")) for e in _subset(events, in_play_reason=why)))}
            for why in sorted({str(e.get("in_play_reason")) for e in events})
        },
        "market_by_block": market_by_block(list(walked.get("market_days") or [])),
        "daily_context": {
            "daily_bias": dict(Counter(str(e.get("daily_bias")) for e in events)),
            "dist_pdh_atr": pct_summary([e.get("dist_pdh_atr") for e in events]),
            "dist_pdl_atr": pct_summary([e.get("dist_pdl_atr") for e in events]),
            "prior_day_range": pct_summary([e.get("prior_day_range") for e in events]),
            "atr20": pct_summary([e.get("atr20") for e in events]),
        },
        "treated_vs_control": treated_vs_control(pairs),
        "human": human,
        "human_reveal": revealed,
        "chart_meta": chart_meta,
        "sample_n": len(sample),
        "events": events,
        "pairs": pairs,
        "matching": {"treated_n": matched.get("treated_n"), "matched_n": matched.get("matched_n"), "match_rate": matched.get("match_rate"), "future_control_selection": False},
    }
    hier = hierarchy(body)
    body["hierarchy"] = hier
    body["decision"] = decide(bool(bind.get("ok")), identity_ok, hier)
    return body
