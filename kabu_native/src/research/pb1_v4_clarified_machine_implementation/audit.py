"""DEVELOPMENT_PARITY_ONLY on the 88 reviewed charts. Not face validation."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_implementation.isolation import FACE_RCA_CACHE
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

REQUIRED_REGRESSIONS = (
    ("3382", "20241004"),
    ("7011", "20250523"),
    ("6758", "20250613"),
    ("4063", "20251118"),
    ("7011", "20241205"),
    ("6920", "20250404"),
    ("9501", "20251113"),
)

# Previous corrected-machine S1 FPs (19) and FNs (2). Mapping, not a target accuracy.
PREV_S1_FP = (
    (1, "7011", "20241205", "MICRO_OR_LEAK"),
    (8, "1605", "20250318", "TWO_SIDED_OPEN"),
    (9, "8031", "20250225", "TWO_SIDED_OPEN"),
    (11, "7741", "20250314", "TWO_SIDED_OPEN"),
    (13, "6701", "20250203", "TWO_SIDED_OPEN"),
    (16, "8002", "20250109", "LATE_RANGE_RESOLUTION"),
    (17, "6920", "20250404", "MICRO_OR_LEAK"),
    (19, "6367", "20250702", "TWO_SIDED_OPEN"),
    (41, "7203", "20250729", "TWO_SIDED_OPEN"),
    (43, "5803", "20250904", "LATE_RANGE_RESOLUTION"),
    (44, "6871", "20250922", "LATE_RANGE_RESOLUTION"),
    (45, "6525", "20250917", "TWO_SIDED_OPEN"),
    (46, "9501", "20251113", "MICRO_OR_LEAK"),
    (52, "4519", "20250826", "LATE_RANGE_RESOLUTION"),
    (56, "5801", "20251022", "LATE_RANGE_RESOLUTION"),
    (64, "8058", "20250801", "TWO_SIDED_OPEN"),
    (67, "6501", "20250724", "LATE_RANGE_RESOLUTION"),
    (70, "2914", "20241022", "TWO_SIDED_OPEN"),
    (78, "9501", "20250311", "LATE_RANGE_RESOLUTION"),
)
PREV_S1_FN = (
    (22, "7011", "20250523", "FAILED_OPEN_THEN_REAL_DRIVE"),
    (66, "6758", "20250613", "FAILED_OPEN_THEN_REAL_DRIVE"),
)

# LATE examples that must not remain valid merely because 09:15 seed was TRUE.
LATE_ACTIVE_EXAMPLES = (
    ("6871", "20250922"),
    ("5801", "20251022"),
    ("4519", "20250826"),
    ("6501", "20250724"),
    ("9501", "20250311"),
)


def _load_rca_rows() -> list[dict[str, Any]]:
    path = FACE_RCA_CACHE / "descriptor_slim.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _idx(funnel: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in funnel:
        out[(str(r.get("symbol")), str(r.get("date")))] = r
    return out


def _exec_idx(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        out[(str(r.get("symbol")), str(r.get("date")))] = r
    return out


def _confusion_layer(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for h, m in pairs if h and m)
    tn = sum(1 for h, m in pairs if (not h) and (not m))
    fp = sum(1 for h, m in pairs if (not h) and m)
    fn = sum(1 for h, m in pairs if h and (not m))
    n = len(pairs)
    return {"n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "accuracy": (tp + tn) / n if n else None}


def _seed_match(human: str, machine: str) -> bool:
    if human == "TRUE_OPENING_DRIVE" and machine in ("TRUE_OPENING_DRIVE", "TRUE_OPENING_DRIVE_SEED"):
        return True
    if human == "FAILED_OPEN_THEN_REAL_DRIVE" and machine in (
        "FAILED_OPEN_THEN_REAL_DRIVE",
        "FAILED_OPEN_SEED",
    ):
        return True
    if human == machine:
        return True
    if human in ("TWO_SIDED_OPEN", "FLAT_OR_CRAWL", "MICRO_OR_LEAK", "LATE_RANGE_RESOLUTION") and machine == human:
        return True
    return False


def audit_88(
    *,
    funnel: list[dict[str, Any]],
    setups: list[dict[str, Any]],
    e0_events: list[dict[str, Any]],
    e1_events: list[dict[str, Any]],
    bind: dict[str, Any],
) -> dict[str, Any]:
    _ = bind
    rca_rows = _load_rca_rows()
    fidx = _idx(funnel)
    sidx = _exec_idx(setups)
    e0idx = _exec_idx(e0_events)
    e1idx = _exec_idx(e1_events)
    rows: list[dict[str, Any]] = []
    seed_p: list[tuple[bool, bool]] = []
    active_p: list[tuple[bool, bool]] = []
    loc_p: list[tuple[bool, bool]] = []
    thesis_p: list[tuple[bool, bool]] = []
    e0_p: list[tuple[bool, bool]] = []
    e1_p: list[tuple[bool, bool]] = []
    for r in rca_rows:
        rid = int(r.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        key = (str(r.get("symbol")), str(r.get("date")))
        f = dict(fidx.get(key) or {})
        human_open = str(lab.get("sp_opening_state") or "")
        h_seed_valid = human_open in VALID_OPENING_STATES
        h_active = h_seed_valid and not bool(lab.get("sp_late"))
        h_loc = str(lab.get("sp_location") or "") == "CLEAR_DEFENDED_LOCATION"
        h_thesis = bool(h_active and h_loc)
        h_e1 = str(lab.get("sp_trigger") or "") == "VALID_REACCELERATION"
        m_seed_valid = str(f.get("OPENING_DRIVE_SEED") or "") in ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED")
        m_active = bool(f.get("OPENING_DRIVE_ACTIVE"))
        m_loc = bool(f.get("LOCATION_IDENTIFIED"))
        m_thesis = bool(f.get("THESIS_READY"))
        m_e0 = bool(f.get("E0"))
        m_e1 = bool(f.get("E1"))
        seed_p.append((h_seed_valid, m_seed_valid))
        active_p.append((h_active, m_active))
        loc_p.append((h_loc, m_loc))
        thesis_p.append((h_thesis, m_thesis))
        e0_p.append((h_thesis, m_e0))
        e1_p.append((h_e1, m_e1))
        rows.append(
            {
                "rca_id": rid,
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "direction_v32": r.get("direction"),
                "human_pattern": lab.get("sp_pattern"),
                "human_opening_state": human_open,
                "human_location": lab.get("sp_location"),
                "human_trigger": lab.get("sp_trigger"),
                "human_late": bool(lab.get("sp_late")),
                "machine_WHY": bool(f.get("WHY_THIS_STOCK")),
                "machine_SEED": f.get("OPENING_DRIVE_SEED"),
                "machine_ACTIVE": m_active,
                "machine_LOCATION": m_loc,
                "machine_THESIS_READY": m_thesis,
                "machine_E0": m_e0,
                "machine_E1": m_e1,
                "machine_opening_state": f.get("opening_state"),
                "machine_DIR": f.get("DIR"),
                "machine_death": f.get("death"),
                "location_family": f.get("location_family"),
                "interaction": f.get("interaction"),
                "opening_drive_id": f.get("opening_drive_id"),
                "location_id": f.get("location_id"),
                "thesis_id": f.get("thesis_id"),
                "seed_state_match": _seed_match(human_open, str(f.get("opening_state") or f.get("OPENING_DRIVE_SEED") or "")),
                "setup_exists": key in sidx,
                "e0_entry_t": (e0idx.get(key) or {}).get("entry_t"),
                "e1_entry_t": (e1idx.get(key) or {}).get("entry_t"),
            }
        )

    def _pick(sym: str, date: str) -> dict[str, Any] | None:
        for r in rows:
            if str(r.get("symbol")) == str(sym) and str(r.get("date")) == str(date):
                return r
        return None

    regressions = [{**dict(_pick(a, b) or {"symbol": a, "date": b, "missing": True}), "required": True} for a, b in REQUIRED_REGRESSIONS]
    fp_map = []
    for rid, sym, date, human in PREV_S1_FP:
        rec = dict(_pick(sym, date) or {"symbol": sym, "date": date, "missing": True})
        rec["prev_s1_fp"] = True
        rec["prev_human"] = human
        rec["rca_id"] = rid
        rec["now_seed"] = rec.get("machine_SEED")
        rec["now_active"] = rec.get("machine_ACTIVE")
        rec["now_opening_state"] = rec.get("machine_opening_state")
        fp_map.append(rec)
    fn_map = []
    for rid, sym, date, human in PREV_S1_FN:
        rec = dict(_pick(sym, date) or {"symbol": sym, "date": date, "missing": True})
        rec["prev_s1_fn"] = True
        rec["prev_human"] = human
        rec["rca_id"] = rid
        rec["now_seed"] = rec.get("machine_SEED")
        rec["now_active"] = rec.get("machine_ACTIVE")
        fn_map.append(rec)
    late_map = []
    for sym, date in LATE_ACTIVE_EXAMPLES:
        rec = dict(_pick(sym, date) or {"symbol": sym, "date": date, "missing": True})
        rec["must_not_remain_active_merely_because_seed_true"] = True
        rec["still_thesis_ready"] = rec.get("machine_THESIS_READY")
        late_map.append(rec)

    mismatches = [
        {
            "rca_id": r.get("rca_id"),
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "human_opening_state": r.get("human_opening_state"),
            "machine_opening_state": r.get("machine_opening_state"),
            "machine_SEED": r.get("machine_SEED"),
            "machine_ACTIVE": r.get("machine_ACTIVE"),
            "machine_death": r.get("machine_death"),
            "layer": "SEED" if not r.get("seed_state_match") else ("ACTIVE" if r.get("human_late") and r.get("machine_THESIS_READY") else None),
        }
        for r in rows
        if (not r.get("seed_state_match")) or (r.get("human_late") and r.get("machine_THESIS_READY"))
    ]

    return {
        "label": "DEVELOPMENT_PARITY_ONLY",
        "not_face_validation": True,
        "n": len(rows),
        "confusion": {
            "SEED_taxonomy": _confusion_layer(seed_p),
            "ACTIVE_state": _confusion_layer(active_p),
            "LOCATION_IDENTIFIED": _confusion_layer(loc_p),
            "THESIS_READY": _confusion_layer(thesis_p),
            "E0": _confusion_layer(e0_p),
            "E1": _confusion_layer(e1_p),
        },
        "human_opening_states": dict(Counter(str(r.get("human_opening_state")) for r in rows)),
        "machine_opening_states": dict(Counter(str(r.get("machine_opening_state")) for r in rows)),
        "required_regressions": regressions,
        "prev_s1_fp19": fp_map,
        "prev_s1_fn2": fn_map,
        "late_false_positive_examples": late_map,
        "material_mismatches": mismatches,
        "rows": rows,
        "3382_20241004": _pick("3382", "20241004"),
        "7011_20250523": _pick("7011", "20250523"),
        "6758_20250613": _pick("6758", "20250613"),
        "4063_20251118": _pick("4063", "20251118"),
        "7011_20241205": _pick("7011", "20241205"),
        "6920_20250404": _pick("6920", "20250404"),
        "9501_20251113": _pick("9501", "20251113"),
    }
