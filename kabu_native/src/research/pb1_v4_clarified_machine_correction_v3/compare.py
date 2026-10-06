"""Old 33b1bf vs new correction. Semantic-row diff only. No accuracy target."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_correction_v3.isolation import CORRECTION_V2_CACHE, FACE_RCA_CACHE

COMPARE_KEYS = (
    "OPENING_DRIVE_SEED",
    "opening_state",
    "OPENING_DRIVE_ACTIVE",
    "LOCATION_IDENTIFIED",
    "location_family",
    "THESIS_READY",
    "E0",
    "E1",
    "death",
    "DIR",
    "failed_open_form",
    "location_A_class",
)


def _rca_cause(old: dict[str, Any], new: dict[str, Any]) -> str:
    o_seed = str(old.get("OPENING_DRIVE_SEED") or "")
    n_seed = str(new.get("OPENING_DRIVE_SEED") or "")
    o_open = str(old.get("opening_state") or "")
    n_open = str(new.get("opening_state") or "")
    o_fam = str(old.get("location_family") or "")
    n_fam = str(new.get("location_family") or "")
    if ("FAILED_OPEN" in o_seed or "FAILED_OPEN" in o_open) != ("FAILED_OPEN" in n_seed or "FAILED_OPEN" in n_open):
        return "FAILED_OPEN_VISIBLE_ATTEMPT"
    if o_seed != n_seed or o_open != n_open:
        if "TWO_SIDED" in o_open or "TWO_SIDED" in n_open:
            return "SEED_TWO_SIDED_LOGIC"
        if "TRUE" in o_seed or "TRUE" in n_seed:
            return "SEED_TWO_SIDED_LOGIC" if "TWO_SIDED" in n_open else "ONE_BAR_PATCH_REMOVAL"
        return "FAILED_OPEN_VISIBLE_ATTEMPT"
    if o_fam.startswith("A") and not n_fam.startswith("A"):
        return "FAMILY_A_CLEAR_SEMANTICS"
    if bool(old.get("THESIS_READY")) != bool(new.get("THESIS_READY")) or bool(old.get("OPENING_DRIVE_ACTIVE")) != bool(
        new.get("OPENING_DRIVE_ACTIVE")
    ):
        return "ACTIVE_MEANINGFUL_PROGRESS"
    if bool(old.get("E0")) != bool(new.get("E0")) or bool(old.get("E1")) != bool(new.get("E1")):
        return "ACTIVE_MEANINGFUL_PROGRESS"
    return "COLLATERAL_STATE_CHANGE"


def _idx(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        out[(str(r.get("symbol")), str(r.get("date")))] = r
    return out


def compare_parent_vs_new(*, new_funnel: list[dict[str, Any]], new_e0: list[dict[str, Any]], new_e1: list[dict[str, Any]]) -> dict[str, Any]:
    path = CORRECTION_V2_CACHE / "walked.json"
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
        diffs = {}
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
        changed.append(
            {
                "rca_id": rid,
                "symbol": key[0],
                "date": key[1],
                "human_opening_state": lab.get("sp_opening_state"),
                "human_pattern": lab.get("sp_pattern"),
                "old_state": {
                    "seed": ov.get("OPENING_DRIVE_SEED"),
                    "opening_state": ov.get("opening_state"),
                    "active": ov.get("OPENING_DRIVE_ACTIVE"),
                    "location": ov.get("LOCATION_IDENTIFIED"),
                    "family": ov.get("location_family"),
                    "thesis": ov.get("THESIS_READY"),
                    "E0": bool(ov.get("E0")) or key in old_e0,
                    "E1": bool(ov.get("E1")) or key in old_e1,
                    "death": ov.get("death"),
                    "e0_entry_t": oe0,
                    "e1_entry_t": oe1,
                },
                "new_state": {
                    "seed": nv.get("OPENING_DRIVE_SEED"),
                    "opening_state": nv.get("opening_state"),
                    "active": nv.get("OPENING_DRIVE_ACTIVE"),
                    "location": nv.get("LOCATION_IDENTIFIED"),
                    "family": nv.get("location_family"),
                    "A_class": nv.get("location_A_class"),
                    "thesis": nv.get("THESIS_READY"),
                    "E0": bool(nv.get("E0")) or key in new_e0i,
                    "E1": bool(nv.get("E1")) or key in new_e1i,
                    "death": nv.get("death"),
                    "failed_open_form": nv.get("failed_open_form"),
                    "e0_entry_t": ne0,
                    "e1_entry_t": ne1,
                },
                "diffs": diffs,
                "rca_cause": _rca_cause(ov, nv),
                "better_final_reject_shortcut": False,
            }
        )
    by_cause: dict[str, int] = {}
    for x in changed:
        k = str(x.get("rca_cause") or "other")
        by_cause[k] = int(by_cause.get(k) or 0) + 1
    return {
        "parent_cache_loaded": path.is_file(),
        "rca_n": len(rca_rows),
        "changed_n": len(changed),
        "unchanged_n": max(0, len(rca_rows) - len(changed)),
        "by_rca_cause": by_cause,
        "rows": changed,
        "accuracy_target": False,
        "future_outcome_used": False,
    }
