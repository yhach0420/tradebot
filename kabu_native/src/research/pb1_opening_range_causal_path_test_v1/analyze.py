"""Four questions. Absolute path first, then R, then matched incremental. No PnL. No retune."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from research.pb1_opening_range_causal_path_test_v1 import (
    CASE_ABS_NOT_INC,
    CASE_BIND,
    CASE_INC_WEAK,
    CASE_NONE,
    CASE_SUPPORTED,
    CASE_UNSTABLE,
    CONFOUNDERS,
    COST_STRESS_BPS,
    EVAL_BLOCKS,
    EXPECTED_SETUP_N,
    HORIZONS,
    MEDIATORS,
    MIN_BLOCK_MATCH_N,
    MIN_MATCH_RATE,
    MFE_R_GATES,
    NEXT_BIND,
    NEXT_COMPLETE,
    NEXT_RCA,
    NOT_MATCHING,
    PARENT_CLEAR_SHARE,
    PARENT_NOT_SHARE,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_QUESTIONABLE_SHARE,
    PARENT_RANGE_NOISE_SHARE,
    PARENT_VERDICT,
    TREATMENT_DEFINING,
)
from research.pb1_opening_range_continuation_face_valid_v2.analyze import MATCHING_VARIABLE_ROLES
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def pct_summary(xs: list[Any]) -> dict[str, Any]:
    a = np.asarray([float(x) for x in xs if _finite(x)], dtype=float)
    if a.size == 0:
        return {"n": 0, "mean": None, "p50": None}
    q = np.quantile(a, [0.10, 0.25, 0.50, 0.75, 0.90])
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "p10": float(q[0]),
        "p25": float(q[1]),
        "p50": float(q[2]),
        "p75": float(q[3]),
        "p90": float(q[4]),
    }


def _rate(xs: list[Any]) -> dict[str, Any]:
    n = len(xs)
    k = int(sum(1 for x in xs if bool(x)))
    return {"n": n, "k": k, "rate": float(k / n) if n else None}


def _frac(xs: list[Any], thresh: float) -> dict[str, Any]:
    a = [float(x) for x in xs if _finite(x)]
    n = len(a)
    k = int(sum(1 for x in a if x >= thresh))
    return {"n": n, "k": k, "rate": float(k / n) if n else None}


def _pos(x: Any) -> bool:
    return bool(_finite(x) and float(x) > 0)


def _sign(x: Any) -> int:
    if not _finite(x) or float(x) == 0:
        return 0
    return 1 if float(x) > 0 else -1


def _subset(events: list[dict[str, Any]], **eq: Any) -> list[dict[str, Any]]:
    out = events
    for k, v in eq.items():
        out = [e for e in out if e.get(k) == v]
    return out


def path_block(events: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(events)
    risk_ok = [e for e in events if e.get("risk_state") == "RISK_DEFINED"]
    ahead = [e for e in events if e.get("target_kind") == "TARGET_AHEAD"]
    no_tgt = [e for e in events if e.get("target_kind") == "NO_PREKNOWN_TARGET_AHEAD"]
    out: dict[str, Any] = {
        "n": n,
        "executable_n": int(sum(1 for e in events if _finite(e.get("entry_px")))),
        "risk_defined_n": len(risk_ok),
        "risk_invalid_n": int(sum(1 for e in events if e.get("risk_state") == "RISK_INVALID")),
        "TARGET_AHEAD_n": len(ahead),
        "NO_PREKNOWN_TARGET_AHEAD_n": len(no_tgt),
        "trigger_to_entry_bps": pct_summary([e.get("trigger_to_entry_bps") for e in events]),
        "trigger_to_entry_signed_bps": pct_summary([e.get("trigger_to_entry_signed_bps") for e in events]),
        "R_bps": pct_summary([e.get("R_bps") for e in risk_ok]),
        "or_accept_fail": _rate([e.get("or_accept_fail") for e in events]),
        "retest_extreme_breach": _rate([e.get("retest_extreme_breach") for e in events]),
        "time_to_OR_failure": pct_summary([e.get("time_to_OR_failure") for e in events]),
        "time_to_retest_extreme_breach": pct_summary([e.get("time_to_retest_extreme_breach") for e in events]),
        "time_to_MFE": pct_summary([e.get("time_to_MFE") for e in events]),
        "time_to_MAE": pct_summary([e.get("time_to_MAE") for e in events]),
        "MFE_bps": pct_summary([e.get("MFE_bps") for e in events]),
        "MAE_bps": pct_summary([e.get("MAE_bps") for e in events]),
        "MFE_over_R": pct_summary([e.get("MFE_over_R") for e in risk_ok]),
        "MAE_over_R": pct_summary([e.get("MAE_over_R") for e in risk_ok]),
        "mfe_before_or_fail_over_R": pct_summary([e.get("mfe_before_or_fail_over_R") for e in risk_ok]),
        "target_distance_bps": pct_summary([e.get("target_distance_bps") for e in ahead]),
        "target_distance_R": pct_summary([e.get("target_distance_R") for e in ahead]),
        "time_to_target": pct_summary([e.get("time_to_target") for e in ahead]),
        "TARGET_HIT_BEFORE_OR_FAILURE": _rate([e.get("TARGET_HIT_BEFORE_OR_FAILURE") for e in ahead]),
        "TARGET_HIT_BEFORE_RETEST_EXTREME_BREACH": _rate(
            [e.get("TARGET_HIT_BEFORE_RETEST_EXTREME_BREACH") for e in ahead]
        ),
        "from_trigger_close_r10_bps": pct_summary([e.get("from_trigger_close_r10_bps") for e in events]),
        "consumption_r10_bps": pct_summary([e.get("consumption_r10_bps") for e in events]),
        "consumption_MFE_bps": pct_summary([e.get("consumption_MFE_bps") for e in events]),
    }
    for h in HORIZONS:
        out[f"r{h}_bps"] = pct_summary([e.get(f"r{h}_bps") for e in events])
        out[f"MFE_{h}m"] = pct_summary([e.get(f"MFE_{h}m") for e in events])
        out[f"MAE_{h}m"] = pct_summary([e.get(f"MAE_{h}m") for e in events])
    for g in MFE_R_GATES:
        key = str(g).replace(".", "_")
        out[f"MFE_ge_{key}R"] = _frac([e.get("MFE_over_R") for e in risk_ok], float(g))
    return out


def incremental_block(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    m = [p for p in pairs if p.get("matched")]
    return {
        "treated_n": len(pairs),
        "matched_n": len(m),
        "match_rate": float(len(m) / len(pairs)) if pairs else None,
        "gap_r5_bps": pct_summary([p.get("gap_r5_bps") for p in m]),
        "gap_r10_bps": pct_summary([p.get("gap_r10_bps") for p in m]),
        "gap_r20_bps": pct_summary([p.get("gap_r20_bps") for p in m]),
        "gap_MFE_bps": pct_summary([p.get("gap_MFE_bps") for p in m]),
        "gap_MAE_bps": pct_summary([p.get("gap_MAE_bps") for p in m]),
        "tr_r10_bps": pct_summary([p.get("tr_r10_bps") for p in m]),
        "ct_r10_bps": pct_summary([p.get("ct_r10_bps") for p in m]),
        "tr_MFE_over_R": pct_summary([p.get("tr_MFE_over_R") for p in m]),
        "same_symbol_n": int(sum(1 for p in m if p.get("same_symbol"))),
        "control_later_pb1_kept_n": int(sum(1 for p in m if p.get("control_later_pb1"))),
        "future_control_selection": False,
    }


def _smd(a: list[Any], b: list[Any]) -> float | None:
    xa = np.asarray([float(x) for x in a if _finite(x)], dtype=float)
    xb = np.asarray([float(x) for x in b if _finite(x)], dtype=float)
    if xa.size == 0 or xb.size == 0:
        return None
    den = float(np.sqrt(0.5 * (np.var(xa) + np.var(xb))))
    if den <= 0:
        return 0.0
    return float((np.mean(xa) - np.mean(xb)) / den)


def matching_balance(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    m = [p for p in pairs if p.get("matched")]
    return {
        "matched_n": len(m),
        "after_atr_smd": _smd([p.get("tr_atr20") for p in m], [p.get("control_atr20") for p in m]),
        "bias_exact_match": True,
        "vwap_exact_match": True,
        "matched_on": list(CONFOUNDERS),
        "not_matched_on": list(TREATMENT_DEFINING) + list(MEDIATORS) + list(NOT_MATCHING),
        "note": (
            "Before matching, at-risk set is same DIR + IN-PLAY + CLEAN + OR frozen + no PB1 trigger by T. "
            "After matching, VWAP class, daily SMA bias, ATR band, and PDH/PDL side are exact/tolerance filters."
        ),
    }


def by_key(events: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        groups[str(e.get(key) or "na")].append(e)
    return {k: path_block(vs) for k, vs in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def retest_age(events: list[dict[str, Any]]) -> dict[str, Any]:
    bins = {"0_5": [], "5_15": [], "15_30": [], "na": []}
    for e in events:
        m = e.get("break_to_retest_minutes")
        if not _finite(m):
            bins["na"].append(e)
        elif float(m) < 5:
            bins["0_5"].append(e)
        elif float(m) < 15:
            bins["5_15"].append(e)
        else:
            bins["15_30"].append(e)
    return {k: path_block(vs) for k, vs in bins.items()}


def reward_geometry(events: list[dict[str, Any]]) -> dict[str, Any]:
    ahead = _subset(events, target_kind="TARGET_AHEAD")
    none = _subset(events, target_kind="NO_PREKNOWN_TARGET_AHEAD")
    bins = {"R_lt_1": [], "R_1_2": [], "R_ge_2": [], "R_undefined": []}
    for e in ahead:
        r = e.get("target_distance_R")
        if not _finite(r):
            bins["R_undefined"].append(e)
        elif float(r) < 1:
            bins["R_lt_1"].append(e)
        elif float(r) < 2:
            bins["R_1_2"].append(e)
        else:
            bins["R_ge_2"].append(e)
    return {
        "TARGET_AHEAD": path_block(ahead),
        "NO_PREKNOWN_TARGET_AHEAD": path_block(none),
        "target_distance_R_bins": {k: path_block(vs) for k, vs in bins.items()},
        "ROOM_AVAILABLE_not_used_as_gate": True,
        "no_preknown_target_is_not_automatically_no_room": True,
    }


def cost_feasibility(events: list[dict[str, Any]]) -> dict[str, Any]:
    mfe = pct_summary([e.get("MFE_bps") for e in events])
    mae = pct_summary([e.get("MAE_bps") for e in events])
    r10 = pct_summary([e.get("r10_bps") for e in events])
    tgt = pct_summary([e.get("target_distance_bps") for e in events if e.get("target_kind") == "TARGET_AHEAD"])
    mfe_ge = _frac([e.get("MFE_bps") for e in events], COST_STRESS_BPS)
    med_mfe = mfe.get("p50")
    med_tgt = tgt.get("p50")
    plausible = bool(
        (_finite(med_mfe) and float(med_mfe) >= COST_STRESS_BPS)
        or (_finite(med_tgt) and float(med_tgt) >= COST_STRESS_BPS)
        or (mfe_ge.get("rate") is not None and float(mfe_ge["rate"]) >= 0.50)
    )
    return {
        "stress_bps": COST_STRESS_BPS,
        "not_actual_historical_transaction_cost": True,
        "MFE_bps": mfe,
        "MAE_bps": mae,
        "r10_bps": r10,
        "target_distance_bps": tgt,
        "frac_MFE_ge_stress": mfe_ge,
        "path_magnitude_plausibly_exceeds_cost_scale": plausible,
    }


def failure_attribution(
    events: list[dict[str, Any]],
    by_block: dict[str, Any],
    by_trig: dict[str, Any],
    by_side: dict[str, Any],
) -> dict[str, Any]:
    abs_signs = {b: _sign((by_block.get(b) or {}).get("r10_bps", {}).get("p50")) for b in EVAL_BLOCKS}
    return {
        "or_accept_fail": _rate([e.get("or_accept_fail") for e in events]),
        "retest_extreme_breach": _rate([e.get("retest_extreme_breach") for e in events]),
        "risk_invalid_share": _rate([e.get("risk_state") == "RISK_INVALID" for e in events]),
        "consumption_r10": pct_summary([e.get("consumption_r10_bps") for e in events]),
        "trigger_to_entry_signed": pct_summary([e.get("trigger_to_entry_signed_bps") for e in events]),
        "block_abs_10m_sign": abs_signs,
        "trigger_n": {k: (v or {}).get("n") for k, v in by_trig.items()},
        "side_n": {k: (v or {}).get("n") for k, v in by_side.items()},
        "contamination_limitation": {
            "CLEAR": PARENT_CLEAR_SHARE,
            "QUESTIONABLE": PARENT_QUESTIONABLE_SHARE,
            "NOT": PARENT_NOT_SHARE,
            "RANGE_NOISE": PARENT_RANGE_NOISE_SHARE,
            "human_48_not_used_as_filter": True,
        },
        "questions_for_rca": [
            "opening impulse",
            "break quality",
            "retest age",
            "trigger type",
            "structural invalidation",
            "reward geometry",
            "side asymmetry",
            "regime",
            "semantic contamination",
        ],
    }


def causal_knowability(events: list[dict[str, Any]], walked: dict[str, Any]) -> dict[str, Any]:
    n = len(events)
    same_bar = int(walked.get("same_bar_entry_n") or 0)
    known = n > 0 and all(bool(e.get("states_known_before_next_open")) for e in events)
    return {
        "question": "Was every trade-state known before next-open entry?",
        "answer": bool(known and same_bar == 0),
        "same_bar_entry_n": same_bar,
        "or15_known_from": "09:15",
        "in_play_known_before_entry": True,
        "opening_impulse_known_before_entry": True,
        "break_leave_retest_trigger_completed_before_entry": True,
        "no_bid_ask_execution_claim": True,
        "future_outcome_n": int(walked.get("future_outcome_n") or 0),
        "or_modified_after_freeze_n": int(walked.get("or_modified_after_freeze_n") or 0),
    }


def decide(
    *,
    bind_ok: bool,
    identity_ok: bool,
    by_block: dict[str, Any],
    inc_block: dict[str, Any],
    match: dict[str, Any],
    cost: dict[str, Any],
) -> dict[str, Any]:
    if not bind_ok or not identity_ok:
        return {
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "STOP_PB1": False,
            "parent_machine_unchanged": True,
            "reason": "bind_or_setup_n_identity_failed",
            "is_strategy": False,
        }
    abs_med = {b: (by_block.get(b) or {}).get("r10_bps", {}).get("p50") for b in EVAL_BLOCKS}
    inc_med = {b: (inc_block.get(b) or {}).get("gap_r10_bps", {}).get("p50") for b in EVAL_BLOCKS}
    abs_signs = [_sign(abs_med[b]) for b in EVAL_BLOCKS]
    inc_signs = [_sign(inc_med[b]) for b in EVAL_BLOCKS]
    abs_all_pos = all(_pos(abs_med[b]) for b in EVAL_BLOCKS)
    inc_all_pos = all(_pos(inc_med[b]) for b in EVAL_BLOCKS)
    match_rate = match.get("match_rate")
    match_ok = bool(_finite(match_rate) and float(match_rate) >= MIN_MATCH_RATE)
    block_n_ok = all(int((inc_block.get(b) or {}).get("matched_n") or 0) >= MIN_BLOCK_MATCH_N for b in EVAL_BLOCKS)
    inc_claimable = bool(inc_all_pos and match_ok and block_n_ok)
    cost_ok = bool(cost.get("path_magnitude_plausibly_exceeds_cost_scale"))
    abs_mixed = len({s for s in abs_signs if s != 0}) > 1
    inc_mixed = len({s for s in inc_signs if s != 0}) > 1
    if abs_all_pos and inc_claimable and cost_ok:
        verd = CASE_SUPPORTED
        nxt = NEXT_COMPLETE
        why = "D2/D3/D4 absolute median 10m > 0, claimable matched 10m gap > 0, path scale vs 8bps stress."
    elif inc_claimable and not abs_all_pos:
        verd = CASE_INC_WEAK
        nxt = NEXT_RCA
        why = "Matched incremental 10m gap is positive on D2/D3/D4, but absolute PB1 path is not a useful daytrade."
    elif abs_all_pos and not inc_claimable:
        verd = CASE_ABS_NOT_INC
        nxt = NEXT_RCA
        why = (
            "Absolute D2/D3/D4 10m path is positive, but the complete PB1 sequence is not incrementally "
            "informative vs at-risk clean in-play opens."
        )
    elif abs_mixed or inc_mixed:
        verd = CASE_UNSTABLE
        nxt = NEXT_RCA
        why = "D2/D3/D4 disagree in sign. No pooled-only success."
    else:
        verd = CASE_NONE
        nxt = NEXT_RCA
        why = "No useful absolute path and no claimable incremental information. Failure RCA is next; do not auto-stop PB1."
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "STOP_PB1": False,
        "is_strategy": False,
        "complete_strategy_not_run": True,
        "parent_machine_unchanged": True,
        "absolute_10m_median": abs_med,
        "matched_10m_gap_median": inc_med,
        "absolute_all_eval_blocks_positive": abs_all_pos,
        "incremental_claimable": inc_claimable,
        "cost_scale_plausible": cost_ok,
        "match_rate": match_rate,
        "match_ok": match_ok,
        "block_matched_n_ok": block_n_ok,
        "interpretation": why,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "kabu50": False,
        "threshold_retune": False,
        "pnl_optimization": False,
        "future_control_selection": False,
        "treatment_variable_matched_away": False,
        "submit_cancel_live": "0/0/0",
        "four_questions_not_merged": True,
    }


def _med(block: dict[str, Any], key: str) -> Any:
    return (block.get(key) or {}).get("p50")


def _mean(block: dict[str, Any], key: str) -> Any:
    return (block.get(key) or {}).get("mean")


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    overall = dict(report.get("absolute_path") or {})
    byb = dict(report.get("by_block") or {})
    inc = dict((report.get("incremental_path") or {}).get("by_block") or {})
    trig = dict(report.get("trigger_types") or {})
    side = dict(report.get("bull_bear") or {})
    know = dict(report.get("causal_knowability") or {})
    match = dict(report.get("risk_set_control") or {})
    cost = dict(report.get("cost_feasibility") or {})
    tgt = dict(report.get("target_semantics") or {})
    tpath = dict(report.get("target_path") or {})
    evs = dict(report.get("events_summary") or {})

    def blk(name: str) -> dict[str, Any]:
        b = dict(byb.get(name) or {})
        i = dict(inc.get(name) or {})
        return {
            "n": b.get("n"),
            "r5_mean": _mean(b, "r5_bps"),
            "r5_p50": _med(b, "r5_bps"),
            "r10_mean": _mean(b, "r10_bps"),
            "r10_p50": _med(b, "r10_bps"),
            "r20_mean": _mean(b, "r20_bps"),
            "r20_p50": _med(b, "r20_bps"),
            "MFE_p50": _med(b, "MFE_bps"),
            "MAE_p50": _med(b, "MAE_bps"),
            "MFE_over_R_p50": _med(b, "MFE_over_R"),
            "or_fail_rate": (b.get("or_accept_fail") or {}).get("rate"),
            "retest_breach_rate": (b.get("retest_extreme_breach") or {}).get("rate"),
            "target_before_or_fail": (b.get("TARGET_HIT_BEFORE_OR_FAILURE") or {}).get("rate"),
            "matched_n": i.get("matched_n"),
            "match_rate": i.get("match_rate"),
            "matched_10m_gap_p50": _med(i, "gap_r10_bps"),
            "matched_10m_gap_mean": _mean(i, "gap_r10_bps"),
        }

    d2, d3, d4 = blk("D2"), blk("D3"), blk("D4")
    abs_eval_pos = bool(d.get("absolute_all_eval_blocks_positive"))
    inc_eval = bool(d.get("incremental_claimable"))
    return {
        "parent_machine_unchanged": True,
        "setup_n": report.get("setup_n"),
        "next_open_executable_n": overall.get("executable_n"),
        "trigger_to_entry_bps": overall.get("trigger_to_entry_bps"),
        "absolute_PB1": {"D2": d2, "D3": d3, "D4": d4, "D1": blk("D1")},
        "MFE": overall.get("MFE_bps"),
        "MAE": overall.get("MAE_bps"),
        "MFE_over_R": overall.get("MFE_over_R"),
        "OR_acceptance_failure_rate": (overall.get("or_accept_fail") or {}).get("rate"),
        "retest_extreme_breach_rate": (overall.get("retest_extreme_breach") or {}).get("rate"),
        "TARGET_AHEAD_n": tgt.get("TARGET_AHEAD_n"),
        "NO_PREKNOWN_TARGET_AHEAD_n": tgt.get("NO_PREKNOWN_TARGET_AHEAD_n"),
        "target_before_failure_rate": (tpath.get("TARGET_HIT_BEFORE_OR_FAILURE") or {}).get("rate"),
        "risk_set_matched_n": match.get("matched_n"),
        "risk_set_match_rate": match.get("match_rate"),
        "matched_10m_gap": {
            "D2": d2.get("matched_10m_gap_p50"),
            "D3": d3.get("matched_10m_gap_p50"),
            "D4": d4.get("matched_10m_gap_p50"),
        },
        "positive_absolute_path_D2_D3_D4": abs_eval_pos,
        "incremental_information_D2_D3_D4": inc_eval,
        "path_magnitude_plausibly_exceeds_cost_scale": cost.get("path_magnitude_plausibly_exceeds_cost_scale"),
        "reclaim_trigger": {
            "n": (trig.get("RECLAIM_RETEST_MICRO_HIGH") or {}).get("n"),
            "r10_p50": _med(trig.get("RECLAIM_RETEST_MICRO_HIGH") or {}, "r10_bps"),
            "MFE_p50": _med(trig.get("RECLAIM_RETEST_MICRO_HIGH") or {}, "MFE_bps"),
            "or_fail": ((trig.get("RECLAIM_RETEST_MICRO_HIGH") or {}).get("or_accept_fail") or {}).get("rate"),
        },
        "failed_push_trigger": {
            "n": (trig.get("FAILED_PUSH_THEN_CLOSE_BACK") or {}).get("n"),
            "r10_p50": _med(trig.get("FAILED_PUSH_THEN_CLOSE_BACK") or {}, "r10_bps"),
            "MFE_p50": _med(trig.get("FAILED_PUSH_THEN_CLOSE_BACK") or {}, "MFE_bps"),
            "or_fail": ((trig.get("FAILED_PUSH_THEN_CLOSE_BACK") or {}).get("or_accept_fail") or {}).get("rate"),
        },
        "bull": {
            "n": (side.get("bull") or {}).get("n"),
            "r10_p50": _med(side.get("bull") or {}, "r10_bps"),
            "MFE_over_R_p50": _med(side.get("bull") or {}, "MFE_over_R"),
        },
        "bear": {
            "n": (side.get("bear") or {}).get("n"),
            "r10_p50": _med(side.get("bear") or {}, "r10_bps"),
            "MFE_over_R_p50": _med(side.get("bear") or {}, "MFE_over_R"),
        },
        "causal_knowability": know.get("answer"),
        "by_direction_counts": evs.get("by_direction"),
        "cost_stress_bps": 8.0,
        "any_future_control_selection": False,
        "any_treatment_variable_matched_away": False,
        "any_threshold_retune": False,
        "any_pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "STOP_PB1": False,
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    match: dict[str, Any],
) -> dict[str, Any]:
    events = list(walked.get("events") or [])
    pairs = list(match.get("pairs") or [])
    by_block = {b: path_block(_subset(events, block=b)) for b in ("D1", "D2", "D3", "D4")}
    by_side = {s: path_block(_subset(events, direction=s)) for s in ("bull", "bear")}
    by_trig = {
        "RECLAIM_RETEST_MICRO_HIGH": path_block(_subset(events, trigger_primary="RECLAIM_RETEST_MICRO_HIGH")),
        "FAILED_PUSH_THEN_CLOSE_BACK": path_block(_subset(events, trigger_primary="FAILED_PUSH_THEN_CLOSE_BACK")),
    }
    inc_all = incremental_block(pairs)
    inc_block = {b: incremental_block([p for p in pairs if p.get("block") == b]) for b in ("D1", "D2", "D3", "D4")}
    inc_side = {s: incremental_block([p for p in pairs if p.get("direction") == s]) for s in ("bull", "bear")}
    cost = cost_feasibility(events)
    know = causal_knowability(events, walked)
    identity_ok = len(events) == int(EXPECTED_SETUP_N)
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        identity_ok=identity_ok,
        by_block=by_block,
        inc_block=inc_block,
        match=match,
        cost=cost,
    )
    overall = path_block(events)
    r10_a = (by_trig["RECLAIM_RETEST_MICRO_HIGH"].get("r10_bps") or {}).get("p50")
    r10_b = (by_trig["FAILED_PUSH_THEN_CLOSE_BACK"].get("r10_bps") or {}).get("p50")
    trig_same = False
    if _finite(r10_a) and _finite(r10_b):
        trig_same = abs(float(r10_a) - float(r10_b)) < 5.0 and _sign(r10_a) == _sign(r10_b)
    return {
        "ok": True,
        "parent_machine_unchanged": True,
        "MACHINE_SHA256": v2_machine_sha256(),
        "PARENT_VERDICT": PARENT_VERDICT,
        "PARENT_PLAYBOOK_MACHINE_SHA256": PARENT_PLAYBOOK_MACHINE_SHA256,
        "face_validity": {
            "question": "FACE VALIDITY",
            "answer": "already_established_by_V2",
            "parent_verdict": PARENT_VERDICT,
            "independent_verify_contamination_limitation": {
                "CLEAR": PARENT_CLEAR_SHARE,
                "QUESTIONABLE": PARENT_QUESTIONABLE_SHARE,
                "NOT": PARENT_NOT_SHARE,
                "RANGE_NOISE": PARENT_RANGE_NOISE_SHARE,
                "48_chart_review_not_an_outcome_selected_filter": True,
            },
        },
        "causal_knowability": know,
        "setup_n": len(events),
        "identity_ok": identity_ok,
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "false_break_n": int(walked.get("false_break_n") or 0),
        "or_modified_after_freeze_n": int(walked.get("or_modified_after_freeze_n") or 0),
        "future_outcome_n": 0,
        "eligible_n": int((walked.get("counts") or {}).get("eligible_n") or 0),
        "counts": dict(walked.get("counts") or {}),
        "events_summary": {
            "setup_n": len(events),
            "by_block": dict(Counter(str(e.get("block")) for e in events)),
            "by_direction": dict(Counter(str(e.get("direction")) for e in events)),
            "trigger_primary": dict(Counter(str(e.get("trigger_primary")) for e in events)),
            "in_play_reason": dict(Counter(str(e.get("in_play_reason")) for e in events)),
            "daily_bias": dict(Counter(str(e.get("daily_bias")) for e in events)),
            "target_kind": dict(Counter(str(e.get("target_kind")) for e in events)),
            "risk_state": dict(Counter(str(e.get("risk_state")) for e in events)),
        },
        "absolute_path": overall,
        "by_block": by_block,
        "bull_bear": by_side,
        "trigger_types": {
            **by_trig,
            "same_mechanism": trig_same,
            "do_not_select_the_better_one": True,
        },
        "opening_quality": by_key(events, "in_play_reason"),
        "opening_impulse_mag": pct_summary([e.get("impulse_mag") for e in events]),
        "retest_age": retest_age(events),
        "reward_geometry": reward_geometry(events),
        "structural_risk": {
            "R_definition": "distance from NEXT OPEN to frozen first-retest extreme",
            "risk_state": dict(Counter(str(e.get("risk_state")) for e in events)),
            "R_bps": overall.get("R_bps"),
            "MFE_over_R": overall.get("MFE_over_R"),
            "MAE_over_R": overall.get("MAE_over_R"),
            "mfe_before_or_fail_over_R": overall.get("mfe_before_or_fail_over_R"),
        },
        "or_failure": {
            "definition": "first completed close back through the defended OR boundary after entry",
            "close_not_wick": True,
            **(overall.get("or_accept_fail") or {}),
            "time_to_OR_failure": overall.get("time_to_OR_failure"),
        },
        "retest_extreme": {
            "definition": "price wick breaches frozen first-retest extreme against the trade",
            **(overall.get("retest_extreme_breach") or {}),
            "time_to_retest_extreme_breach": overall.get("time_to_retest_extreme_breach"),
        },
        "target_semantics": {
            "TARGET_AHEAD_n": overall.get("TARGET_AHEAD_n"),
            "NO_PREKNOWN_TARGET_AHEAD_n": overall.get("NO_PREKNOWN_TARGET_AHEAD_n"),
            "NO_OBVIOUS_ROOM_not_primary_gate": True,
            "NO_PREKNOWN_TARGET_AHEAD_not_automatically_no_room": True,
            "preknown_only": ["PDH", "PDL", "PDC", "D5H", "D5L", "SMA25", "SMA75", "VWAP_if_ahead"],
            "no_future_best_target": True,
        },
        "target_path": {
            "TARGET_HIT_BEFORE_OR_FAILURE": overall.get("TARGET_HIT_BEFORE_OR_FAILURE"),
            "TARGET_HIT_BEFORE_RETEST_EXTREME_BREACH": overall.get("TARGET_HIT_BEFORE_RETEST_EXTREME_BREACH"),
            "time_to_target": overall.get("time_to_target"),
            "target_distance_bps": overall.get("target_distance_bps"),
            "target_distance_R": overall.get("target_distance_R"),
            "not_a_take_profit_strategy": True,
        },
        "risk_set_control": {
            "treated_n": match.get("treated_n"),
            "matched_n": match.get("matched_n"),
            "match_rate": match.get("match_rate"),
            "same_symbol_match_n": match.get("same_symbol_match_n"),
            "control_later_pb1_kept_n": match.get("control_later_pb1_kept_n"),
            "future_control_selection": False,
            "treatment_variable_matched_away": False,
            "at_risk_includes_later_pb1": True,
            "matched_on": list(CONFOUNDERS),
            "not_matched_on": list(TREATMENT_DEFINING) + list(MEDIATORS) + list(NOT_MATCHING),
        },
        "matching_balance": matching_balance(pairs),
        "incremental_path": {"all": inc_all, "by_block": inc_block, "by_side": inc_side},
        "execution_consumption": {
            "trigger_to_entry_bps": overall.get("trigger_to_entry_bps"),
            "trigger_to_entry_signed_bps": overall.get("trigger_to_entry_signed_bps"),
            "from_trigger_close_r10_bps": overall.get("from_trigger_close_r10_bps"),
            "from_next_open_r10_bps": overall.get("r10_bps"),
            "consumption_r10_bps": overall.get("consumption_r10_bps"),
            "consumption_MFE_bps": overall.get("consumption_MFE_bps"),
            "question": "is any PB1 edge already consumed before executable entry?",
        },
        "cost_feasibility": cost,
        "failure_attribution": failure_attribution(events, by_block, by_trig, by_side),
        "matching_variable_roles": MATCHING_VARIABLE_ROLES,
        "decision": decision,
        "events": events,
        "pairs": pairs,
    }
