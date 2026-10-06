"""V3.2 summaries. No PnL. Development confusion only. FACE_VALID unknown."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    CASE_BIND,
    CASE_FROZEN_WAIT,
    MEANINGFUL_LEAVE_NOISE_MULT,
    MEANINGFUL_R_NOISE_MULT,
    NEXT_BIND,
    NEXT_WAIT,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PARENT_V31_SHA,
    PRIMARY_TRIGGER,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import RULE_DIFF, STATE_MACHINE_TEXT, machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.isolation import CAPTURE_ROOT, CONTEXT_CAPTURE_ROOT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def funnel_report(counts: dict[str, Any], n_s0: int) -> dict[str, Any]:
    names = {
        "S0": "genuine in-play",
        "S1": "clean opening drive V3",
        "S2": "real break",
        "S3": "meaningful leave",
        "S4": "first retest",
        "S5": "meaningful defended location",
        "S6": "meaningful structural R",
        "S7": "structural route clear",
        "S8": "reacceleration trigger V2",
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


def unseen_inventory(bind: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Count unseen sources. Do not review charts. Do not open Confirmation/FV."""
    ban: set[tuple[str, str]] = set()
    for a, b, _c in set(bind.get("v1_dev_keys") or set()):
        ban.add((str(a), str(b)))
    for name in ("v2_verify_keys", "trigger_rca96_keys", "structure_rca96_keys", "v3_face_keys"):
        for tup in bind.get(name) or set():
            if len(tup) >= 2:
                ban.add((str(tup[0]), str(tup[1])))
    ban |= set(bind.get("v3_face_symbol_dates") or set())
    unseen = [e for e in events if (str(e.get("symbol")), str(e.get("date"))) not in ban]
    post_hist = 0
    capture_dates: list[str] = []
    for root in (CAPTURE_ROOT, CONTEXT_CAPTURE_ROOT):
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if p.is_dir() and p.name.isdigit() and str(p.name) > "20260911":
                capture_dates.append(str(p.name))
    capture_dates = sorted(set(capture_dates))
    return {
        "discovery_unseen_event_n": len(unseen),
        "discovery_seen_overlap_n": len(events) - len(unseen),
        "excluded_symbol_date_n": len(ban),
        "post_20260911_historical_candidate_n": post_hist,
        "post_20260911_historical_note": "Research 1m panel PANEL_TO is frozen at 20260911. Dates after that are not in the Discovery parquet.",
        "prospective_capture_date_n": len(capture_dates),
        "prospective_capture_dates": capture_dates,
        "prospective_capture_event_n": 0,
        "independent_face_sample_n": 0,
        "independent_review_run": False,
        "label_if_none": "WAIT_FOR_PROSPECTIVE_UNSEEN_FACE_VERIFY_V1",
        "POST_RESEARCH_CUTOFF_FACE_VALIDATION_ONLY": False,
        "PROSPECTIVE_CAPTURE_FACE_VALIDATION": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "charts_not_rendered_until_frozen_then_unseen": True,
    }


def write_exclusion_manifest(out_dir: Path, bind: dict[str, Any], unseen_meta: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(
        {
            f"{a}|{b}|{c}"
            for name in ("v1_dev_keys", "v2_verify_keys", "trigger_rca96_keys", "structure_rca96_keys", "v3_face_keys")
            for a, b, *rest in (bind.get(name) or set())
            for c in ([rest[0]] if rest else ["*"])
        }
    )
    payload = {
        "face_review_exclusion_keys": keys,
        "post_20260911_reviewed_symbol_dates": [],
        "prospective_capture_reviewed_symbol_dates": [],
        "note": "Once a post-cutoff symbol-date is chart-reviewed it is permanently contaminated for pristine economic validation. None reviewed in V3.2.",
        "unseen_meta": {k: v for k, v in unseen_meta.items() if k != "prospective_capture_dates"} | {
            "prospective_capture_dates": list(unseen_meta.get("prospective_capture_dates") or [])
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "face_review_exclusion_manifest.json").write_text(
        __import__("json").dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def decide(bind_ok: bool, identity_ok: bool, unseen_meta: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "FACE_VALID": False}
    return {
        "VERDICT": CASE_FROZEN_WAIT,
        "NEXT": NEXT_WAIT,
        "FACE_VALID": False,
        "FACE_VALID_from_development_set": False,
        "FACE_VALID_UNKNOWN": True,
        "independent_face_sample_n": int(unseen_meta.get("independent_face_sample_n") or 0),
        "WAIT_FOR_PROSPECTIVE_UNSEEN_FACE_VERIFY_V1": True,
        "retest_5min_gate": False,
        "clock_0930_cutoff": False,
        "new_indicator": False,
        "reclaim_threshold_from_profit": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "pnl_optimization": False,
        "identity_ok": identity_ok,
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    audit: dict[str, Any],
    unseen_meta: dict[str, Any],
) -> dict[str, Any]:
    events = list(walked.get("events") or [])
    counts = dict(walked.get("counts") or {})
    v32_sha = machine_sha256()
    v2_live = v2_machine_sha256()
    v3_live = v3_machine_sha256()
    v31_live = v31_machine_sha256()
    identity_ok = (
        v2_live == PARENT_V2_SHA
        and v3_live == PARENT_V3_SHA
        and v31_live == PARENT_V31_SHA
        and int(walked.get("same_bar_entry_n") or 0) == 0
    )
    funnel = funnel_report(counts, int(counts.get("S0") or 0))
    deaths = death_report(counts, list(walked.get("funnel_days") or []))
    decision = decide(bool(bind.get("ok")), identity_ok, unseen_meta)
    trigs = Counter(str(e.get("trigger_primary")) for e in events)
    auctions = Counter(str(e.get("auction")) for e in events)
    return {
        "MACHINE_SHA256": v32_sha,
        "PARENT_V31_SHA": PARENT_V31_SHA,
        "PARENT_V3_SHA": PARENT_V3_SHA,
        "PARENT_V2_SHA": PARENT_V2_SHA,
        "live_v31_sha": v31_live,
        "live_v3_sha": v3_live,
        "live_v2_sha": v2_live,
        "v31_unchanged": v31_live == PARENT_V31_SHA,
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
        "CLEAN_OPENING_DRIVE_V3": True,
        "REACCELERATION_TRIGGER_V2": True,
        "reclaim_1n1m_gated": False,
        "reclaim_threshold_from_profit": False,
        "retest_5min_gate": False,
        "clock_0930_cutoff": False,
        "new_indicator": False,
        "MEANINGFUL_R_NOISE_MULT": MEANINGFUL_R_NOISE_MULT,
        "MEANINGFUL_LEAVE_NOISE_MULT": MEANINGFUL_LEAVE_NOISE_MULT,
        "acceptance_fail_closes": ACCEPTANCE_FAIL_CLOSES,
        "pnl_test": False,
        "funnel": funnel,
        "deaths": deaths,
        "trigger_primary_counts": dict(trigs),
        "auction_counts": dict(auctions),
        "identity_ok": identity_ok,
        "audit_old67": audit,
        "unseen": unseen_meta,
        "walk_counts": counts,
        "decision": decision,
    }
