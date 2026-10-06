"""V3 face-validity summaries. No PnL. No matching. No threshold search."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3 import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    CASE_BIND,
    CASE_FACE_VALID,
    CASE_NEEDS_REBUILD,
    GATE_BLOCKED_LEAK_MAX,
    GATE_CLEAR_MIN,
    GATE_FAILED_PUSH_LEAK,
    GATE_NOT_MAX,
    GATE_RISK_INVALID,
    NEXT_BIND,
    NEXT_FIX,
    NEXT_PATH_TEST,
    PARENT_V2_SHA,
    PRIMARY_TRIGGER,
    STRUCTURAL_ROUTE_R_MULT,
)
from research.pb1_playbook_redesign_v3.charts import sampling_procedure
from research.pb1_playbook_redesign_v3.definitions import RULE_DIFF, STATE_MACHINE_TEXT, machine_sha256


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def funnel_report(counts: dict[str, Any], n_s0: int) -> dict[str, Any]:
    names = {
        "S0": "in-play + clean impulse",
        "S1": "real break",
        "S2": "leave",
        "S3": "first retest",
        "S4": "defended location",
        "S5": "structural route valid",
        "S6": "reclaim trigger",
        "S7": "next-open executable",
    }
    out: dict[str, Any] = {}
    for st, name in names.items():
        n = int(counts.get(st) or 0)
        out[st] = {"n": n, "name": name, "of_S0": float(n / n_s0) if n_s0 else None}
    return out


def death_report(counts: dict[str, Any], funnel_days: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "FAILED_PUSH_NOT_PB1",
        "NO_DEFENDED_LOCATION",
        "STRUCTURALLY_BLOCKED",
        "MOVE_ALREADY_REACHED_STRUCTURE",
        "RETEST_EXTREME_INVALID",
        "NO_RECLAIM",
        "STALE_30M",
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
    out["failed_push_archive_n"] = int(counts.get("FAILED_PUSH_NOT_PB1") or 0)
    return out


def decide(bind_ok: bool, identity_ok: bool, human: dict[str, Any], walked: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "FACE_VALID": False, "eligibility_changed": False}
    n = int(human.get("reviewed_n") or 0)
    clear_share = human.get("clear_share")
    not_share = float((human.get("NOT_CONTINUATION") or 0) / n) if n else None
    leak = human.get("structurally_blocked_leak_share")
    fp_leak = int(walked.get("failed_push_leak_n") or 0)
    risk_inv = int(walked.get("risk_invalid_n") or 0)
    labels_ok = bool(human.get("actual_manual_blinded_review"))
    face = bool(
        identity_ok
        and labels_ok
        and n > 0
        and _finite(clear_share)
        and float(clear_share) >= float(GATE_CLEAR_MIN)
        and (not_share is None or float(not_share) <= float(GATE_NOT_MAX))
        and (leak is None or float(leak) <= float(GATE_BLOCKED_LEAK_MAX))
        and fp_leak == int(GATE_FAILED_PUSH_LEAK)
        and risk_inv == int(GATE_RISK_INVALID)
    )
    return {
        "VERDICT": CASE_FACE_VALID if face else CASE_NEEDS_REBUILD,
        "NEXT": NEXT_PATH_TEST if face else NEXT_FIX,
        "FACE_VALID": face,
        "STOP_PB1": False,
        "is_strategy": False,
        "eligibility_changed": False,
        "threshold_optimized": False,
        "pnl_optimization": False,
        "market_regime_gate": False,
        "symbol_archetype_gate": False,
        "clean_flip_winner_rule": False,
        "retest_5min_gate": False,
        "extension_bps_optimized": False,
        "one_r_searched": False,
        "failed_push_removed_for_return": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "gate": {
            "CLEAR_min": GATE_CLEAR_MIN,
            "NOT_max": GATE_NOT_MAX,
            "blocked_leak_max": GATE_BLOCKED_LEAK_MAX,
            "failed_push_leak": GATE_FAILED_PUSH_LEAK,
            "risk_invalid": GATE_RISK_INVALID,
            "clear_share": clear_share,
            "not_share": not_share,
            "blocked_leak": leak,
            "failed_push_leak_n": fp_leak,
            "risk_invalid_n": risk_inv,
            "actual_manual_review": labels_ok,
        },
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    sample: list[dict[str, Any]],
    chart_meta: list[dict[str, Any]],
    human: dict[str, Any],
) -> dict[str, Any]:
    events = list(walked.get("events") or [])
    counts = dict(walked.get("counts") or {})
    v3_sha = machine_sha256()
    v2_live = v2_machine_sha256()
    identity_ok = v2_live == PARENT_V2_SHA and int(walked.get("same_bar_entry_n") or 0) == 0
    funnel = funnel_report(counts, int(counts.get("S0") or 0))
    deaths = death_report(counts, list(walked.get("funnel_days") or []))
    decision = decide(bool(bind.get("ok")), identity_ok, human, walked)
    trigs = Counter(str(e.get("trigger_primary")) for e in events)
    zones = Counter(str(e.get("zone_class")) for e in events)
    defs = Counter(str(e.get("defended_level_type")) for e in events)
    return {
        "MACHINE_SHA256": v3_sha,
        "PARENT_V2_SHA": PARENT_V2_SHA,
        "live_v2_sha": v2_live,
        "v2_unchanged": v2_live == PARENT_V2_SHA,
        "STATE_MACHINE_TEXT": STATE_MACHINE_TEXT,
        "rule_diff": RULE_DIFF,
        "setup_n": len(events),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "risk_invalid_n": int(walked.get("risk_invalid_n") or 0),
        "failed_push_leak_n": int(walked.get("failed_push_leak_n") or 0),
        "failed_push_archive_n": len(list(walked.get("failed_push_archive") or [])),
        "failed_push_removed_from_pb1": True,
        "failed_push_archived": True,
        "failed_push_removed_for_return": False,
        "primary_trigger": PRIMARY_TRIGGER,
        "archived_trigger": ARCHIVED_TRIGGER,
        "structural_route_defined": (
            f"at trigger close, no known opposing zone intersects "
            f"[close, close + {STRUCTURAL_ROUTE_R_MULT}*planned_R] in trade direction"
        ),
        "why_1R": "unobstructed structural room equal to the amount structurally risked",
        "one_r_searched": False,
        "prior_zone_becomes_support": (
            "if the opening move clears a known opposing zone, V3 continues only when the first "
            "retest tests the far side of that same zone (ZONE_CLEARED_AND_DEFENDED). "
            "Not a CLEAN_FLIP winner rule."
        ),
        "defended_location_defined": "OR boundary tested by the first retest, or a just-cleared zone tested from the far side",
        "exhausted_retest_defined": (
            "before first retest, breakout already touched/entered the next pre-known opposing zone "
            "(MOVE_ALREADY_REACHED_STRUCTURE). Event-based. No extension-bps cutoff."
        ),
        "retest_5min_gate": False,
        "extension_bps_optimized": False,
        "acceptance_fail_closes": ACCEPTANCE_FAIL_CLOSES,
        "invalidation_primary": "RETEST_EXTREME_BREACH",
        "invalidation_secondary": "PERSISTENT_ACCEPTANCE_FAILURE",
        "one_close_or_not_immediate_fail": True,
        "market_regime_gate": False,
        "symbol_archetype_gate": False,
        "clean_flip_winner_rule": False,
        "pnl_test": False,
        "funnel": funnel,
        "deaths": deaths,
        "trigger_primary_counts": dict(trigs),
        "zone_class_counts": dict(zones),
        "defended_type_counts": dict(defs),
        "identity_ok": identity_ok,
        "sample_n": len(sample),
        "sampling_procedure": sampling_procedure(),
        "chart_meta": chart_meta,
        "human": human,
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
                "zone_class": e.get("zone_class"),
                "defended_level_type": e.get("defended_level_type"),
            }
            for e in sample
        ],
    }
