"""Attribute V3 FACE_VERIFY 67 using V3.1 semantics. No future returns."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from research.pb1_playbook_redesign_v3.classify import HUMAN_LABELS as V3_HUMAN
from research.pb1_v3_1_face_validity_fix import MEANINGFUL_LEAVE_NOISE_MULT, MEANINGFUL_R_NOISE_MULT
from research.pb1_v3_1_face_validity_fix.drive import classify_drive
from research.pb1_v3_1_face_validity_fix.isolation import V3_CACHE
from research.pb1_v3_1_face_validity_fix.machine import _finite


CAUSE_KEYS = (
    "NON_DIRECTIONAL_OPEN",
    "OPENING_IMPULSE_LOST",
    "MICRO_OR_LEAK",
    "MICRO_STRUCTURE_NOT_TRADABLE",
    "STRUCTURALLY_BLOCKED",
    "MOVE_ALREADY_REACHED_STRUCTURE",
    "STALE_RETEST",
    "WEAK_RECLAIM",
    "OTHER",
)


def _load_v3_events() -> dict[tuple[str, str, str], dict[str, Any]]:
    path = V3_CACHE / "walked.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in list(payload.get("events") or []):
        out[(str(e.get("symbol")), str(e.get("date")), str(e.get("direction")))] = e
    return out


def _dir_sign(direction: str) -> int:
    return 1 if str(direction) == "bull" else -1


def _add(causes: list[str], name: str) -> None:
    if name not in causes:
        causes.append(name)


def attribute_old67(
    *,
    v3_human_rows: list[dict[str, Any]],
    v31_events: list[dict[str, Any]],
    v31_funnel: list[dict[str, Any]],
    minutes_by_key: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Existing 67 are semantic development data only. Future returns unused."""
    _ = minutes_by_key
    emit_idx = {(str(e.get("symbol")), str(e.get("date")), str(e.get("direction"))): e for e in v31_events}
    v3_idx = _load_v3_events()
    funnel_idx = defaultdict(list)
    for d in v31_funnel:
        funnel_idx[(str(d.get("symbol")), str(d.get("date")))].append(d)
    rows = []
    cause_n = Counter()
    overlap_n = Counter()
    still_pat = Counter()
    drop_pat = Counter()
    drop_death = Counter()
    non_clear = 0
    for rec in v3_human_rows:
        sid = int(rec.get("sample_id") or 0)
        lab = dict(V3_HUMAN.get(sid) or {})
        if not lab:
            continue
        pattern = str(lab.get("pattern") or "")
        key3 = (str(rec.get("symbol")), str(rec.get("date")), str(rec.get("direction")))
        v31 = emit_idx.get(key3)
        v3e = v3_idx.get(key3) or {}
        want = _dir_sign(key3[2])
        funs = [f for f in funnel_idx.get((key3[0], key3[1]), []) if int(f.get("DIR") or 0) in (0, want)]
        death = None
        impulse_lost = False
        for f in funs:
            death = f.get("death") or death
            impulse_lost = impulse_lost or bool(f.get("impulse_lost")) or str(f.get("death")) == "OPENING_IMPULSE_LOST"
        if v31 is not None:
            still_pat[pattern] += 1
        else:
            drop_pat[pattern] += 1
            drop_death[str(death or "none")] += 1
        drive = classify_drive(v3e) if v3e else {"state": None, "DIR": 0}
        drive_ok = drive.get("state") == "CLEAN_OPENING_DRIVE_V2" and int(drive.get("DIR") or 0) == want
        n1m = None
        if v31 is not None and _finite(v31.get("NORMAL_1M_RANGE")):
            n1m = float(v31.get("NORMAL_1M_RANGE"))
        else:
            med = (v3e.get("planned_R_units") or {}).get("med_1m") if isinstance(v3e.get("planned_R_units"), dict) else None
            if _finite(med):
                n1m = float(med)
        max_away = v3e.get("max_away")
        r_px = v3e.get("planned_R")
        causes: list[str] = []
        if pattern != "CLEAR_CONTINUATION":
            non_clear += 1
            if (not lab.get("B_impulse_directional")) or (not drive_ok) or str(death) == "NON_DIRECTIONAL_OPEN":
                _add(causes, "NON_DIRECTIONAL_OPEN")
            if impulse_lost:
                _add(causes, "OPENING_IMPULSE_LOST")
            if str(death) == "MICRO_OR_LEAK" or (
                _finite(n1m) and _finite(max_away) and float(max_away) < float(MEANINGFUL_LEAVE_NOISE_MULT) * float(n1m)
            ):
                _add(causes, "MICRO_OR_LEAK")
            if str(death) == "MICRO_STRUCTURE_NOT_TRADABLE" or (
                _finite(n1m) and _finite(r_px) and float(r_px) < float(MEANINGFUL_R_NOISE_MULT) * float(n1m)
            ):
                _add(causes, "MICRO_STRUCTURE_NOT_TRADABLE")
            if str(lab.get("location")) == "STRUCTURALLY_BLOCKED" or str(death) == "STRUCTURALLY_BLOCKED":
                _add(causes, "STRUCTURALLY_BLOCKED")
            if lab.get("H_already_reached_objective") or str(death) == "MOVE_ALREADY_REACHED_STRUCTURE":
                _add(causes, "MOVE_ALREADY_REACHED_STRUCTURE")
            if lab.get("I_already_extended_stale") or str(lab.get("retest_lab")) == "EXHAUSTED_RETEST":
                _add(causes, "STALE_RETEST")
            if (not lab.get("F_micro_reclaim_reasonable")) or str(lab.get("trigger_lab")) == "WEAK_RECLAIM":
                _add(causes, "WEAK_RECLAIM")
            if not causes:
                _add(causes, "OTHER")
            for c in causes:
                cause_n[c] += 1
            overlap_n[len(causes)] += 1
        rows.append(
            {
                "sample_id": sid,
                "symbol": rec.get("symbol"),
                "date": rec.get("date"),
                "direction": rec.get("direction"),
                "pattern": pattern,
                "v31_still_emitted": v31 is not None,
                "v31_death": death,
                "machine_drive_state": drive.get("state"),
                "causes": causes,
                "n_causes": len(causes),
                "future_used": False,
            }
        )
    still_n = int(sum(still_pat.values()))
    weak_still = int(still_pat.get("QUESTIONABLE") or 0) + int(still_pat.get("NOT_CONTINUATION") or 0)
    return {
        "n": len(rows),
        "non_clear_n": non_clear,
        "cause_counts": {k: int(cause_n.get(k) or 0) for k in CAUSE_KEYS},
        "overlap_n_causes": dict(overlap_n),
        "rows": rows,
        "used_only_for_semantics": True,
        "future_outcome_used": False,
        "existing_67_used_only_for_semantics": True,
        "semantic_consistency": {
            "label": "NOT_INDEPENDENT_FACE_VALIDATION",
            "old67_n": len(rows),
            "still_emitted_n": still_n,
            "dropped_n": int(sum(drop_pat.values())),
            "still_by_pattern": dict(still_pat),
            "dropped_by_pattern": dict(drop_pat),
            "dropped_by_v31_death": dict(drop_death),
            "weak_or_not_among_still_emitted": weak_still,
            "weak_reclaims_mostly_disappeared": bool(still_n > 0 and weak_still / still_n <= 0.35),
            "independent_face_sample": False,
        },
    }
