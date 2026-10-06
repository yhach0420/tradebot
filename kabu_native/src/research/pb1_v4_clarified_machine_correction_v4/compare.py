"""V3 299fb0ca vs V4. Delta A report-only vs Delta B lifecycle. No accuracy target."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_correction_v4.isolation import CORRECTION_V3_CACHE, FACE_RCA_CACHE

COMPARE_KEYS = (
    "OPENING_DRIVE_SEED",
    "opening_state",
    "OPENING_DRIVE_ACTIVE",
    "OPENING_DRIVE_LIVE",
    "OPENING_DRIVE_REACHED",
    "LOCATION_IDENTIFIED",
    "location_family",
    "THESIS_READY",
    "THESIS_LIVE",
    "THESIS_REACHED",
    "THESIS_LOST",
    "THESIS_LOST_AT",
    "THESIS_LOST_REASON",
    "E0",
    "E1",
    "death",
    "DIR",
    "failed_open_form",
    "location_A_class",
    "post_dominant_class",
)


def _clock(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.split("T", 1)[1]
    if " " in text:
        text = text.split(" ", 1)[1]
    return text[:5]


def _idx(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        out[(str(r.get("symbol")), str(r.get("date")))] = r
    return out


LIFECYCLE_KEYS = {
    "OPENING_DRIVE_SEED",
    "opening_state",
    "OPENING_DRIVE_ACTIVE",
    "OPENING_DRIVE_LIVE",
    "OPENING_DRIVE_REACHED",
    "LOCATION_IDENTIFIED",
    "location_family",
    "THESIS_READY",
    "THESIS_LIVE",
    "THESIS_REACHED",
    "THESIS_LOST",
    "THESIS_LOST_AT",
    "THESIS_LOST_REASON",
    "E0",
    "E1",
    "death",
    "DIR",
    "failed_open_form",
    "location_A_class",
    "e0_entry_t",
    "e1_entry_t",
}


def _change_reason(diffs: dict[str, Any], nv: dict[str, Any]) -> str:
    keys = set(diffs)
    if keys <= {"post_dominant_class", "POST_DOMINANT_PATH_STATE"}:
        return "DELTA_A_REPORT_ONLY"
    reason = str(nv.get("THESIS_LOST_REASON") or nv.get("death") or "")
    if "FAILED_BREAK_REACCEPTED" in reason or "FAILED_BREAK_REACCEPTED" in str(diffs.get("THESIS_LOST_REASON") or {}):
        return "DELTA_B_FAILED_BREAK_REACCEPTED"
    if keys & {"THESIS_LIVE", "THESIS_LOST", "THESIS_LOST_AT", "THESIS_LOST_REASON", "OPENING_DRIVE_LIVE", "OPENING_DRIVE_ACTIVE", "death"}:
        return "DELTA_B_LIFECYCLE"
    if keys & {"E0", "E1", "e0_entry_t", "e1_entry_t"}:
        return "DELTA_B_EXECUTION"
    return "COLLATERAL_STATE_CHANGE"


def compare_parent_vs_new(*, new_funnel: list[dict[str, Any]], new_e0: list[dict[str, Any]], new_e1: list[dict[str, Any]]) -> dict[str, Any]:
    path = CORRECTION_V3_CACHE / "walked.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    old_funnel = list(old.get("funnel_days") or [])
    old_e0 = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(old.get("e0_events") or [])}
    old_e1 = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(old.get("e1_events") or [])}
    new_e0i = {(str(r.get("symbol")), str(r.get("date"))): r for r in new_e0}
    new_e1i = {(str(r.get("symbol")), str(r.get("date"))): r for r in new_e1}
    rca_rows = []
    slim = FACE_RCA_CACHE / "descriptor_slim.json"
    if slim.is_file():
        rca_rows = json.loads(slim.read_text(encoding="utf-8"))
    oidx = _idx(old_funnel)
    nidx = _idx(new_funnel)
    changed: list[dict[str, Any]] = []
    for r in rca_rows:
        key = (str(r.get("symbol")), str(r.get("date")))
        ov = dict(oidx.get(key) or {})
        nv = dict(nidx.get(key) or {})
        diffs: dict[str, Any] = {}
        for k in COMPARE_KEYS:
            a, b = ov.get(k), nv.get(k)
            if k in ("E0", "E1"):
                a = bool(ov.get(k)) or (key in old_e0 if k == "E0" else key in old_e1)
                b = bool(nv.get(k)) or (key in new_e0i if k == "E0" else key in new_e1i)
            if a != b:
                diffs[k] = {"old": a, "new": b}
        oe0 = (old_e0.get(key) or {}).get("entry_t")
        ne0 = (new_e0i.get(key) or {}).get("entry_t")
        oe1 = (old_e1.get(key) or {}).get("entry_t")
        ne1 = (new_e1i.get(key) or {}).get("entry_t")
        if oe0 != ne0:
            diffs["e0_entry_t"] = {"old": oe0, "new": ne0}
        if oe1 != ne1:
            diffs["e1_entry_t"] = {"old": oe1, "new": ne1}
        if not diffs:
            continue
        rid = int(r.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        reason = _change_reason(diffs, nv)
        lifecycle = bool(set(diffs) & LIFECYCLE_KEYS)
        changed.append(
            {
                "rca_id": rid,
                "symbol": key[0],
                "date": key[1],
                "human_opening_state": lab.get("sp_opening_state"),
                "human_pattern": lab.get("sp_pattern"),
                "V3_SEED": ov.get("OPENING_DRIVE_SEED"),
                "V4_SEED": nv.get("OPENING_DRIVE_SEED"),
                "V3_ACTIVE_REACHED": ov.get("OPENING_DRIVE_REACHED") or bool(ov.get("opening_drive_id")),
                "V4_ACTIVE_REACHED": nv.get("OPENING_DRIVE_REACHED") or bool(nv.get("opening_drive_id")),
                "V3_ACTIVE_LIVE": ov.get("OPENING_DRIVE_LIVE") if ov.get("OPENING_DRIVE_LIVE") is not None else ov.get("OPENING_DRIVE_ACTIVE"),
                "V4_ACTIVE_LIVE": nv.get("OPENING_DRIVE_LIVE") if nv.get("OPENING_DRIVE_LIVE") is not None else nv.get("OPENING_DRIVE_ACTIVE"),
                "V3_THESIS_REACHED": ov.get("THESIS_REACHED") or bool(ov.get("thesis_id")),
                "V4_THESIS_REACHED": nv.get("THESIS_REACHED") or bool(nv.get("thesis_id")),
                "V3_THESIS_LIVE": ov.get("THESIS_LIVE") if ov.get("THESIS_LIVE") is not None else ov.get("THESIS_READY"),
                "V4_THESIS_LIVE": nv.get("THESIS_LIVE") if nv.get("THESIS_LIVE") is not None else nv.get("THESIS_READY"),
                "V3_THESIS_LOST_AT": ov.get("THESIS_LOST_AT"),
                "V4_THESIS_LOST_AT": nv.get("THESIS_LOST_AT"),
                "V3_E0": bool(ov.get("E0")) or key in old_e0,
                "V4_E0": bool(nv.get("E0")) or key in new_e0i,
                "V3_E1": bool(ov.get("E1")) or key in old_e1,
                "V4_E1": bool(nv.get("E1")) or key in new_e1i,
                "V3_E1_T": oe1,
                "V4_E1_T": ne1,
                "V3_post_dominant_class": ov.get("post_dominant_class"),
                "V4_post_dominant_class": nv.get("post_dominant_class"),
                "change_reason": reason,
                "delta_a_report_only": reason == "DELTA_A_REPORT_ONLY",
                "delta_b_lifecycle": lifecycle and reason != "DELTA_A_REPORT_ONLY",
                "same_failed_break_reaccepted_semantic": reason == "DELTA_B_FAILED_BREAK_REACCEPTED",
                "old_state": {
                    "seed": ov.get("OPENING_DRIVE_SEED"),
                    "opening_state": ov.get("opening_state"),
                    "active_live": ov.get("OPENING_DRIVE_LIVE") if ov.get("OPENING_DRIVE_LIVE") is not None else ov.get("OPENING_DRIVE_ACTIVE"),
                    "thesis_live": ov.get("THESIS_LIVE") if ov.get("THESIS_LIVE") is not None else ov.get("THESIS_READY"),
                    "lost_at": ov.get("THESIS_LOST_AT"),
                    "lost_reason": ov.get("THESIS_LOST_REASON"),
                    "E0": bool(ov.get("E0")) or key in old_e0,
                    "E1": bool(ov.get("E1")) or key in old_e1,
                    "post_dominant_class": ov.get("post_dominant_class"),
                    "e1_entry_t": oe1,
                },
                "new_state": {
                    "seed": nv.get("OPENING_DRIVE_SEED"),
                    "opening_state": nv.get("opening_state"),
                    "active_live": nv.get("OPENING_DRIVE_LIVE") if nv.get("OPENING_DRIVE_LIVE") is not None else nv.get("OPENING_DRIVE_ACTIVE"),
                    "thesis_live": nv.get("THESIS_LIVE") if nv.get("THESIS_LIVE") is not None else nv.get("THESIS_READY"),
                    "lost_at": nv.get("THESIS_LOST_AT"),
                    "lost_reason": nv.get("THESIS_LOST_REASON"),
                    "E0": bool(nv.get("E0")) or key in new_e0i,
                    "E1": bool(nv.get("E1")) or key in new_e1i,
                    "post_dominant_class": nv.get("post_dominant_class"),
                    "e1_entry_t": ne1,
                    "auction_end_family": nv.get("auction_end_family"),
                },
                "diffs": diffs,
            }
        )
    by_cause: dict[str, int] = {}
    for x in changed:
        k = str(x.get("change_reason") or "other")
        by_cause[k] = int(by_cause.get(k) or 0) + 1
    failed_break_n = sum(1 for x in changed if x.get("same_failed_break_reaccepted_semantic"))
    report_only_n = sum(1 for x in changed if x.get("delta_a_report_only"))
    lifecycle_n = sum(1 for x in changed if x.get("delta_b_lifecycle"))
    return {
        "parent_cache_loaded": path.is_file(),
        "parent": "CORRECTION_V3",
        "rca_n": len(rca_rows),
        "changed_n": len(changed),
        "unchanged_n": max(0, len(rca_rows) - len(changed)),
        "by_change_reason": by_cause,
        "delta_a_report_only_n": report_only_n,
        "delta_b_lifecycle_n": lifecycle_n,
        "failed_break_reaccepted_n": failed_break_n,
        "rows": changed,
        "accuracy_target": False,
        "future_outcome_used": False,
    }
