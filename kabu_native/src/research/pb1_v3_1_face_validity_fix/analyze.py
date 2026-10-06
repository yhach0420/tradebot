"""V3.1 face-validity summaries. No PnL. No matching. No threshold search."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    CASE_BIND,
    CASE_FACE_VALID,
    CASE_NEEDS_REBUILD,
    CASE_NO_INDEP,
    DRIVE_NET_OR_FRAC,
    GATE_BLOCKED_LEAK_MAX,
    GATE_CLEAR_MIN,
    GATE_FAILED_PUSH_LEAK,
    GATE_MICRO_STRUCTURE_LEAK_MAX,
    GATE_NOT_MAX,
    GATE_RISK_INVALID,
    INDEPENDENT_MIN_N,
    MEANINGFUL_LEAVE_NOISE_MULT,
    MEANINGFUL_R_NOISE_MULT,
    NEXT_BIND,
    NEXT_FIX,
    NEXT_HOLD,
    NEXT_PATH_TEST,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PRIMARY_TRIGGER,
)
from research.pb1_v3_1_face_validity_fix.definitions import RULE_DIFF, STATE_MACHINE_TEXT, machine_sha256


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def funnel_report(counts: dict[str, Any], n_s0: int) -> dict[str, Any]:
    names = {
        "S0": "genuine in-play",
        "S1": "clean opening drive V2",
        "S2": "real break",
        "S3": "meaningful leave",
        "S4": "first retest",
        "S5": "meaningful defended location",
        "S6": "meaningful structural R",
        "S7": "structural route clear",
        "S8": "reclaim trigger",
        "S9": "next-open executable",
    }
    out: dict[str, Any] = {}
    for st, name in names.items():
        n = int(counts.get(st) or 0)
        out[st] = {"n": n, "name": name, "of_S0": float(n / n_s0) if n_s0 else None}
    return out


def death_report(counts: dict[str, Any], funnel_days: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "NON_DIRECTIONAL_OPEN",
        "OPENING_IMPULSE_LOST",
        "MICRO_OR_LEAK",
        "MICRO_STRUCTURE_NOT_TRADABLE",
        "STRUCTURALLY_BLOCKED",
        "MOVE_ALREADY_REACHED_STRUCTURE",
        "STALE_30M",
        "NO_RECLAIM",
        "OTHER",
        "immediate_collapse",
        "failed_retest",
        "or_invalid",
    )
    raw = Counter(str(d.get("death")) for d in funnel_days if d.get("death"))
    mapped = Counter()
    for k, n in raw.items():
        if k in ("immediate_collapse", "failed_retest", "or_invalid"):
            mapped["OTHER"] += n
        else:
            mapped[k] += n
    out = {k: {"count_field": int(counts.get(k) or 0), "day_deaths": int(mapped.get(k) or 0)} for k in keys}
    out["day_death_counts"] = dict(raw)
    return out


def decide(
    bind_ok: bool,
    identity_ok: bool,
    human: dict[str, Any],
    walked: dict[str, Any],
    sample_meta: dict[str, Any],
) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "FACE_VALID": False, "eligibility_changed": False}
    unseen = int(sample_meta.get("unseen_candidate_n") or 0)
    indep_n = int(sample_meta.get("independent_sample_n") or 0)
    n = int(human.get("reviewed_n") or 0)
    clear_share = human.get("clear_share")
    not_share = human.get("not_share")
    leak = None
    fp_leak = int(walked.get("failed_push_leak_n") or 0)
    risk_inv = int(walked.get("risk_invalid_n") or 0)
    micro_leak = int(walked.get("micro_structure_leak_n") or 0)
    micro_share = float(micro_leak / max(1, int(walked.get("counts", {}).get("setup_n") or walked.get("setup_n") or 1)))
    labels_ok = bool(human.get("actual_manual_blinded_review"))
    drive_ag = human.get("opening_drive_human_agreement")
    route_ag = human.get("structural_route_human_agreement")
    independent_unavailable = unseen == 0
    too_small = indep_n > 0 and indep_n < int(INDEPENDENT_MIN_N)
    strong = (
        identity_ok
        and labels_ok
        and n > 0
        and n == indep_n
        and _finite(clear_share)
        and float(clear_share) >= float(GATE_CLEAR_MIN)
        and (not_share is None or float(not_share) <= float(GATE_NOT_MAX))
        and fp_leak == int(GATE_FAILED_PUSH_LEAK)
        and risk_inv == int(GATE_RISK_INVALID)
        and micro_share <= float(GATE_MICRO_STRUCTURE_LEAK_MAX)
        and _finite(drive_ag)
        and float(drive_ag) >= float(GATE_CLEAR_MIN)
        and _finite(route_ag)
        and float(route_ag) >= float(GATE_CLEAR_MIN)
        and indep_n >= int(INDEPENDENT_MIN_N)
    )
    if independent_unavailable:
        verdict, nxt, face = CASE_NO_INDEP, NEXT_HOLD, False
    elif strong:
        verdict, nxt, face = CASE_FACE_VALID, NEXT_PATH_TEST, True
    else:
        verdict, nxt, face = CASE_NEEDS_REBUILD, NEXT_FIX, False
        if too_small and labels_ok:
            nxt = NEXT_HOLD
    return {
        "VERDICT": verdict,
        "NEXT": nxt,
        "FACE_VALID": face,
        "INDEPENDENT_FACE_VERIFY_NOT_AVAILABLE_IN_DISCOVERY": independent_unavailable,
        "independent_sample_too_small": too_small,
        "SEMANTIC_CONSISTENCY_REVIEW": independent_unavailable,
        "NOT_INDEPENDENT_FACE_VALIDATION": independent_unavailable,
        "STOP_PB1": False,
        "is_strategy": False,
        "eligibility_changed": False,
        "threshold_optimized": False,
        "pnl_optimization": False,
        "retest_5min_gate": False,
        "clock_0930_cutoff": False,
        "new_indicator": False,
        "one_r_searched": False,
        "leave_threshold_optimized": False,
        "drive_frac_searched": False,
        "noise_mult_searched": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "gate": {
            "CLEAR_min": GATE_CLEAR_MIN,
            "NOT_max": GATE_NOT_MAX,
            "blocked_leak_max": GATE_BLOCKED_LEAK_MAX,
            "failed_push_leak": GATE_FAILED_PUSH_LEAK,
            "risk_invalid": GATE_RISK_INVALID,
            "micro_structure_leak_max": GATE_MICRO_STRUCTURE_LEAK_MAX,
            "clear_share": clear_share,
            "not_share": not_share,
            "failed_push_leak_n": fp_leak,
            "risk_invalid_n": risk_inv,
            "micro_structure_leak_n": micro_leak,
            "micro_structure_leak_share": micro_share,
            "drive_agreement": drive_ag,
            "route_agreement": route_ag,
            "actual_manual_review": labels_ok,
            "unseen_candidate_n": unseen,
            "independent_sample_n": indep_n,
        },
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    sample: list[dict[str, Any]],
    sample_meta: dict[str, Any],
    chart_meta: list[dict[str, Any]],
    human: dict[str, Any],
    attribution: dict[str, Any],
) -> dict[str, Any]:
    events = list(walked.get("events") or [])
    counts = dict(walked.get("counts") or {})
    v31_sha = machine_sha256()
    v2_live = v2_machine_sha256()
    v3_live = v3_machine_sha256()
    identity_ok = v2_live == PARENT_V2_SHA and v3_live == PARENT_V3_SHA and int(walked.get("same_bar_entry_n") or 0) == 0
    funnel = funnel_report(counts, int(counts.get("S0") or 0))
    deaths = death_report(counts, list(walked.get("funnel_days") or []))
    decision = decide(bool(bind.get("ok")), identity_ok, human, walked, sample_meta)
    trigs = Counter(str(e.get("trigger_primary")) for e in events)
    reclaim_ratios = [
        float(e.get("reclaim_move_over_NORMAL_1M_RANGE"))
        for e in events
        if _finite(e.get("reclaim_move_over_NORMAL_1M_RANGE"))
    ]
    n1m_vals = [float(e.get("NORMAL_1M_RANGE")) for e in events if _finite(e.get("NORMAL_1M_RANGE"))]
    cons = dict((attribution or {}).get("semantic_consistency") or {})
    return {
        "MACHINE_SHA256": v31_sha,
        "PARENT_V3_SHA": PARENT_V3_SHA,
        "PARENT_V2_SHA": PARENT_V2_SHA,
        "live_v3_sha": v3_live,
        "live_v2_sha": v2_live,
        "v3_unchanged": v3_live == PARENT_V3_SHA,
        "v2_unchanged": v2_live == PARENT_V2_SHA,
        "STATE_MACHINE_TEXT": STATE_MACHINE_TEXT,
        "rule_diff": RULE_DIFF,
        "setup_n": len(events),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "risk_invalid_n": int(walked.get("risk_invalid_n") or 0),
        "failed_push_leak_n": int(walked.get("failed_push_leak_n") or 0),
        "micro_structure_leak_n": int(walked.get("micro_structure_leak_n") or 0),
        "failed_push_archive_n": len(list(walked.get("failed_push_archive") or [])),
        "primary_trigger": PRIMARY_TRIGGER,
        "archived_trigger": ARCHIVED_TRIGGER,
        "NORMAL_1M_RANGE_defined": (
            "median completed 1m high-low at the same clock over prior 20 valid trading sessions only"
        ),
        "planned_R_must_exceed_one_local_noise_unit": True,
        "one_r_noise_searched": False,
        "meaningful_leave_must_exceed_one_local_noise_unit": True,
        "leave_threshold_optimized": False,
        "CLEAN_OPENING_IMPULSE_rebuilt": True,
        "DRIVE_NET_OR_FRAC": DRIVE_NET_OR_FRAC,
        "drive_frac_searched": False,
        "MEANINGFUL_R_NOISE_MULT": MEANINGFUL_R_NOISE_MULT,
        "MEANINGFUL_LEAVE_NOISE_MULT": MEANINGFUL_LEAVE_NOISE_MULT,
        "retest_5min_gate": False,
        "clock_0930_cutoff": False,
        "new_indicator": False,
        "acceptance_fail_closes": ACCEPTANCE_FAIL_CLOSES,
        "market_regime_gate": False,
        "symbol_archetype_gate": False,
        "clean_flip_winner_rule": False,
        "pnl_test": False,
        "funnel": funnel,
        "deaths": deaths,
        "trigger_primary_counts": dict(trigs),
        "identity_ok": identity_ok,
        "sample_n": len(sample),
        "sample_meta": sample_meta,
        "unseen_candidate_n": sample_meta.get("unseen_candidate_n"),
        "independent_sample_n": sample_meta.get("independent_sample_n"),
        "chart_meta": chart_meta,
        "human": human,
        "attribution_old67": attribution,
        "semantic_consistency": cons,
        "reclaim_move_over_n1m_lt_1_n": int(sum(1 for x in reclaim_ratios if x < 1.0)),
        "reclaim_move_over_n1m_n": len(reclaim_ratios),
        "reclaim_gated": False,
        "NORMAL_1M_RANGE_event_median": (sorted(n1m_vals)[len(n1m_vals) // 2] if n1m_vals else None),
        "walk_counts": counts,
        "decision": decision,
        "sample": [
            {
                "sample_id": e.get("sample_id"),
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "block": e.get("block"),
                "direction": e.get("direction"),
                "break_t": e.get("break_t"),
                "retest_t": e.get("retest_t"),
                "trigger_t": e.get("trigger_t"),
                "structural_route": e.get("structural_route"),
                "NORMAL_1M_RANGE": e.get("NORMAL_1M_RANGE"),
                "planned_R": e.get("planned_R"),
            }
            for e in sample
        ],
    }
