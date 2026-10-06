"""Semantic development audit on old 67. No future returns. Not independent validation."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from research.pb1_playbook_redesign_v3.classify import HUMAN_LABELS as V3_HUMAN
from research.pb1_v3_1_face_validity_fix.drive import classify_drive as classify_drive_v2
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.isolation import RESEARCH_ROOT, V3_CACHE, V31_CACHE
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.drive import classify_drive as classify_drive_v3

WEAK_CAUSE_KEYS = (
    "tiny_overshoot",
    "weak_body",
    "bad_close_location",
    "no_expansion",
    "drift_crawl",
    "late_stalled",
    "large_opposite_wick",
    "other",
)


def _weak_geometry() -> dict[int, dict[str, Any]]:
    p = RESEARCH_ROOT / "_work" / "pb1_v3_2_tmp_diag.json"
    if not p.is_file():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    out: dict[int, dict[str, Any]] = {}
    for r in list(payload.get("reclaim_rows") or []):
        out[int(r.get("sid") or 0)] = r
    return out


def _load_events(path) -> dict[tuple[str, str, str], dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in list(payload.get("events") or []):
        out[(str(e.get("symbol")), str(e.get("date")), str(e.get("direction")))] = e
    return out


def _funnel(path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    idx: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for d in list(payload.get("funnel_days") or []):
        idx[(str(d.get("symbol")), str(d.get("date")))].append(d)
    return idx


def _dir_sign(direction: str) -> int:
    return 1 if str(direction) == "bull" else -1


def _death_for(funs: list[dict[str, Any]], want: int) -> tuple[str | None, bool]:
    death = None
    lost = False
    for f in funs:
        if int(f.get("DIR") or 0) in (0, want):
            death = f.get("death") or death
            lost = lost or bool(f.get("impulse_lost")) or str(f.get("death")) == "OPENING_IMPULSE_LOST"
    return death, lost


def audit_old67(
    *,
    v3_human_rows: list[dict[str, Any]],
    v32_events: list[dict[str, Any]],
    v32_funnel: list[dict[str, Any]],
) -> dict[str, Any]:
    v3_idx = _load_events(V3_CACHE / "walked.json")
    v31_idx = _load_events(V31_CACHE / "walked.json")
    v31_fun = _funnel(V31_CACHE / "walked.json")
    v32_idx = {(str(e.get("symbol")), str(e.get("date")), str(e.get("direction"))): e for e in v32_events}
    v32_fun: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for d in v32_funnel:
        v32_fun[(str(d.get("symbol")), str(d.get("date")))].append(d)

    dropped_clear: list[dict[str, Any]] = []
    weak_causes = Counter()
    stale_why = Counter()
    geo_idx = _weak_geometry()
    drive_v2_vs_b = Counter()
    drive_v3_vs_b = Counter()
    conf = Counter()
    drive_conf = Counter()
    reaccel_conf = Counter()
    rows = []
    session_open_mismatch_n = 0
    other_clear_drop_n = 0
    for rec in v3_human_rows:
        sid = int(rec.get("sample_id") or 0)
        lab = dict(V3_HUMAN.get(sid) or {})
        if not lab:
            continue
        key3 = (str(rec.get("symbol")), str(rec.get("date")), str(rec.get("direction")))
        want = _dir_sign(key3[2])
        v3e = v3_idx.get(key3) or {}
        v31e = v31_idx.get(key3)
        v32e = v32_idx.get(key3)
        death31, lost31 = _death_for(v31_fun.get((key3[0], key3[1]), []), want)
        death32, _lost32 = _death_for(v32_fun.get((key3[0], key3[1]), []), want)
        d2 = classify_drive_v2(v3e)
        d3 = classify_drive_v3(v3e)
        v2_ok = d2.get("state") == "CLEAN_OPENING_DRIVE_V2" and int(d2.get("DIR") or 0) == want
        v3_ok = d3.get("state") == "CLEAN_OPENING_DRIVE_V3" and int(d3.get("DIR") or 0) == want
        b = bool(lab.get("B_impulse_directional"))
        f = bool(lab.get("F_micro_reclaim_reasonable"))
        drive_v2_vs_b[(v2_ok, b)] += 1
        drive_v3_vs_b[(v3_ok, b)] += 1
        pattern = str(lab.get("pattern") or "")
        still31 = v31e is not None
        still32 = v32e is not None
        conf[(pattern, still32)] += 1
        drive_conf[(b, v3_ok)] += 1
        reaccel_conf[(f, still32)] += 1
        if pattern == "CLEAR_CONTINUATION" and not still31:
            why = str(death31 or "OTHER")
            session_mismatch = (not v2_ok) and why == "NON_DIRECTIONAL_OPEN"
            if session_mismatch:
                session_open_mismatch_n += 1
                why_detail = "SESSION_OPEN_ANCHOR_MISMATCH"
            elif why == "OPENING_IMPULSE_LOST":
                why_detail = "OPENING_IMPULSE_LOST"
            else:
                other_clear_drop_n += 1
                why_detail = "OTHER"
            dropped_clear.append(
                {
                    "sample_id": sid,
                    "symbol": key3[0],
                    "date": key3[1],
                    "direction": key3[2],
                    "v31_death": death31,
                    "v2_drive_ok": v2_ok,
                    "v3_drive_ok": v3_ok,
                    "v32_still_emitted": still32,
                    "why": why_detail,
                    "zone_class": v3e.get("zone_class"),
                    "defended": v3e.get("defended_level_type"),
                    "note": lab.get("note"),
                    "human_chart_looked_clear": True,
                }
            )
        causes: list[str] = []
        if not f:
            geo = geo_idx.get(sid) or {}
            causes = [str(x) for x in list(geo.get("causes") or [])]
            if not causes:
                causes.append("other")
            for c in causes:
                weak_causes[c] += 1
        if lab.get("I_already_extended_stale") or str(lab.get("retest_lab")) == "EXHAUSTED_RETEST":
            if lab.get("H_already_reached_objective"):
                stale_why["objective_already_reached"] += 1
            if not b:
                stale_why["opening_thesis_lost_or_absent"] += 1
            if "sit" in str(lab.get("note") or "").lower() or "grind" in str(lab.get("note") or "").lower():
                stale_why["sat_on_or_or_range_reestablished"] += 1
            if "late" in str(lab.get("note") or "").lower():
                stale_why["late_range_resolution"] += 1
            if not b and str(lab.get("note") or "").lower().find("range") >= 0:
                stale_why["range_reestablished"] += 1
            note_l = str(lab.get("note") or "").lower()
            if "failed" in note_l or "multiple" in note_l:
                stale_why["multiple_failed_attempts"] += 1
            if "not a distinctive" in note_l or "not distinctive" in note_l or "leftover" in note_l:
                stale_why["lack_of_directional_expansion"] += 1
            if not (
                lab.get("H_already_reached_objective")
                or (not b)
                or ("sit" in note_l or "grind" in note_l)
                or ("late" in note_l)
            ):
                stale_why["other"] += 1
        rows.append(
            {
                "sample_id": sid,
                "symbol": key3[0],
                "date": key3[1],
                "direction": key3[2],
                "pattern": pattern,
                "B": b,
                "F": f,
                "v31_emitted": still31,
                "v32_emitted": still32,
                "v31_death": death31,
                "v32_death": death32,
                "v2_drive_ok": v2_ok,
                "v3_drive_ok": v3_ok,
                "auction": d3.get("auction"),
                "weak_causes": causes,
                "future_used": False,
            }
        )

    def _pat_counts(keep: bool) -> dict[str, int]:
        out = {"CLEAR_CONTINUATION": 0, "QUESTIONABLE": 0, "NOT_CONTINUATION": 0}
        for rec in v3_human_rows:
            sid = int(rec.get("sample_id") or 0)
            lab = V3_HUMAN.get(sid) or {}
            key3 = (str(rec.get("symbol")), str(rec.get("date")), str(rec.get("direction")))
            emitted = key3 in v32_idx
            if emitted == keep:
                out[str(lab.get("pattern") or "QUESTIONABLE")] = out.get(str(lab.get("pattern") or "QUESTIONABLE"), 0) + 1
        return out

    retained = _pat_counts(True)
    removed = _pat_counts(False)
    n_b = sum(1 for r in rows if r["B"])
    n_f = sum(1 for r in rows if r["F"])
    return {
        "n": len(rows),
        "used_only_for_semantics": True,
        "future_outcome_used": False,
        "dropped_clear_v31": dropped_clear,
        "dropped_clear_n": len(dropped_clear),
        "dropped_clear_session_open_mismatch_n": session_open_mismatch_n,
        "dropped_clear_other_n": other_clear_drop_n,
        "dropped_clear_impulse_lost_n": int(sum(1 for r in dropped_clear if r.get("why") == "OPENING_IMPULSE_LOST")),
        "weak_reclaim_cause_counts": {k: int(weak_causes.get(k) or 0) for k in WEAK_CAUSE_KEYS} | {
            "n_weak_reclaim": int(sum(1 for r in rows if not r.get("F"))),
            "overlapping": True,
            "geometry_source": "V3 trigger-bar vs NORMAL_1M_RANGE on old 67; not a profit gate",
        },
        "stale_why_counts": dict(stale_why),
        "stale_event_state": (
            "STALE if the opening thesis is already complete or lost before the first retest "
            "(objective reached, range re-established, or leftover leak after the drive finished). "
            "Not a 5-minute clock and not a 09:30 cutoff."
        ),
        "opening_representation_best": "OPPOSITE_OR_EXTREME",
        "opening_representation_note": (
            "09:14 close location vs frozen OR (distance from opposite extreme) matches "
            "human directional-auction calls, including gap-up-sold / early-reversal dumps. "
            "SESSION_OPEN 0.50 net dropped CLEAR events whose OR close was already directional. "
            "FIRST_5M_EXTREME is usually the same extreme as opposite-OR when the spike is early. "
            "Directional efficiency is persisted, not gated."
        ),
        "confusion": {
            "CLEAR_retained": retained.get("CLEAR_CONTINUATION"),
            "CLEAR_lost": removed.get("CLEAR_CONTINUATION"),
            "QUESTIONABLE_retained": retained.get("QUESTIONABLE"),
            "QUESTIONABLE_removed": removed.get("QUESTIONABLE"),
            "NOT_retained": retained.get("NOT_CONTINUATION"),
            "NOT_removed": removed.get("NOT_CONTINUATION"),
            "label": "SEMANTIC_DEVELOPMENT_PERFORMANCE_NOT_INDEPENDENT_VALIDATION",
        },
        "opening_drive_confusion": {
            "human_B_true_machine_drive": int(drive_v3_vs_b.get((True, True), 0)),
            "human_B_true_machine_not": int(drive_v3_vs_b.get((False, True), 0)),
            "human_B_false_machine_drive": int(drive_v3_vs_b.get((True, False), 0)),
            "human_B_false_machine_not": int(drive_v3_vs_b.get((False, False), 0)),
            "v2_agreement_with_B": (int(drive_v2_vs_b.get((True, True), 0)) + int(drive_v2_vs_b.get((False, False), 0))) / max(1, len(rows)),
            "v3_agreement_with_B": (int(drive_v3_vs_b.get((True, True), 0)) + int(drive_v3_vs_b.get((False, False), 0))) / max(1, len(rows)),
            "human_B_n": n_b,
        },
        "reacceleration_confusion": {
            "human_F_true_emitted": int(reaccel_conf.get((True, True), 0)),
            "human_F_true_not_emitted": int(reaccel_conf.get((True, False), 0)),
            "human_F_false_emitted": int(reaccel_conf.get((False, True), 0)),
            "human_F_false_not_emitted": int(reaccel_conf.get((False, False), 0)),
            "human_F_n": n_f,
        },
        "drive_v2_vs_b": {f"{a}_{b}": n for (a, b), n in drive_v2_vs_b.items()},
        "drive_v3_vs_b": {f"{a}_{b}": n for (a, b), n in drive_v3_vs_b.items()},
        "rows": rows,
        "FACE_VALID_from_development_set": False,
    }
