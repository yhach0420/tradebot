"""Semantic development audit on the 88 reviewed charts. DEVELOPMENT FIT ONLY. Not face validation."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_machine_implementation.isolation import RCA_CACHE
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES
from research.pb1_v4_opening_drive_location_reaccel_spec.exemplars import CURATED_NEGATIVES, map_positives


REQUIRED_NEGATIVES = (
    ("7011", "20241205", "bull"),
    ("6963", "20241227", "bull"),
    ("9983", "20241205", None),
    ("9432", "20250402", "bear"),
    ("9501", "20250311", "bear"),
    ("8001", "20250609", "bull"),
    ("8035", "20250701", "bear"),
    ("7182", "20251112", "bull"),
)

REQUIRED_SPECIALS = (
    ("3382", "20241004", "bear"),
    ("6787", "20250214", "bull"),
    ("4063", "20251118", "bear"),
)


def _load_rca_rows() -> list[dict[str, Any]]:
    path = RCA_CACHE / "descriptor_slim.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


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


def audit_88(
    *,
    funnel: list[dict[str, Any]],
    setups: list[dict[str, Any]],
    e0_events: list[dict[str, Any]],
    e1_events: list[dict[str, Any]],
    bind: dict[str, Any],
) -> dict[str, Any]:
    rca_rows = _load_rca_rows()
    fidx = _idx(funnel)
    sidx = _exec_idx(setups)
    e0idx = _exec_idx(e0_events)
    e1idx = _exec_idx(e1_events)
    rows: list[dict[str, Any]] = []
    s0_p: list[tuple[bool, bool]] = []
    s1_p: list[tuple[bool, bool]] = []
    s2_p: list[tuple[bool, bool]] = []
    s3_p: list[tuple[bool, bool]] = []
    s4_p: list[tuple[bool, bool]] = []
    e1_p: list[tuple[bool, bool]] = []
    for r in rca_rows:
        rid = int(r.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        key = (str(r.get("symbol")), str(r.get("date")))
        f = dict(fidx.get(key) or {})
        setup = sidx.get(key)
        e0 = e0idx.get(key)
        e1 = e1idx.get(key)
        h_s0 = str(lab.get("sp_opening_state") or "") != "FLAT_OR_CRAWL"
        h_s1 = str(lab.get("sp_opening_state") or "") in VALID_OPENING_STATES
        h_s2 = str(lab.get("sp_location") or "") == "CLEAR_DEFENDED_LOCATION"
        h_s3 = str(lab.get("sp_retest") or "") in ("VALID_FIRST_RETEST", "FIRST_RETEST", "") or h_s2
        if str(lab.get("sp_retest") or "") == "STALE_OR_EXHAUSTED":
            h_s3 = False
        h_s4 = bool(h_s1 and h_s2 and not lab.get("sp_late"))
        h_e1 = str(lab.get("sp_trigger") or "") == "VALID_REACCELERATION"
        m_s0 = bool(f.get("S0"))
        m_s1 = bool(f.get("S1"))
        m_s2 = bool(f.get("S2"))
        m_s3 = bool(f.get("S3"))
        m_s4 = bool(f.get("S4"))
        m_e1 = bool(f.get("E1"))
        s0_p.append((h_s0, m_s0))
        s1_p.append((h_s1, m_s1))
        s2_p.append((h_s2, m_s2))
        s3_p.append((h_s3, m_s3))
        s4_p.append((h_s4, m_s4))
        e1_p.append((h_e1, m_e1))
        rows.append(
            {
                "rca_id": rid,
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "direction_v32": r.get("direction"),
                "human_pattern": lab.get("sp_pattern"),
                "human_opening_state": lab.get("sp_opening_state"),
                "human_location": lab.get("sp_location"),
                "human_trigger": lab.get("sp_trigger"),
                "machine_S0": m_s0,
                "machine_S1": m_s1,
                "machine_S2": m_s2,
                "machine_S3": m_s3,
                "machine_S4": m_s4,
                "machine_E0": bool(f.get("E0")),
                "machine_E1": m_e1,
                "machine_opening_state": f.get("opening_state"),
                "machine_DIR": f.get("DIR"),
                "machine_death": f.get("death"),
                "location_family": f.get("location_family"),
                "SETUP_ELIGIBLE_AT": f.get("SETUP_ELIGIBLE_AT"),
                "setup_exists": setup is not None,
                "e0_entry_t": (e0 or {}).get("entry_t"),
                "e1_entry_t": (e1 or {}).get("entry_t"),
            }
        )

    def _pick(sym: str, date: str) -> dict[str, Any] | None:
        for r in rows:
            if str(r.get("symbol")) == str(sym) and str(r.get("date")) == str(date):
                return r
        return None

    specials = [{**dict(_pick(a, b) or {"symbol": a, "date": b, "missing": True}), "required": True} for a, b, _d in REQUIRED_SPECIALS]
    negatives = []
    for a, b, _d in REQUIRED_NEGATIVES:
        rec = _pick(a, b) or {"symbol": a, "date": b, "missing": True}
        negatives.append(rec)

    positives = map_positives(bind)
    pos_map = []
    for p in positives:
        rec = _pick(str(p.get("symbol")), str(p.get("date"))) or {}
        pos_map.append(
            {
                **p,
                "machine_S1": rec.get("machine_S1"),
                "machine_S2": rec.get("machine_S2"),
                "machine_S4": rec.get("machine_S4"),
                "machine_E0": rec.get("machine_E0"),
                "machine_E1": rec.get("machine_E1"),
                "machine_opening_state": rec.get("machine_opening_state"),
                "machine_death": rec.get("machine_death"),
                "location_family": rec.get("location_family"),
            }
        )

    s1_ok = 0
    for r in rows:
        h = str(r.get("human_opening_state") or "")
        m = str(r.get("machine_opening_state") or "")
        if h == m or (h in VALID_OPENING_STATES and r.get("machine_S1") and m in VALID_OPENING_STATES):
            s1_ok += 1

    return {
        "label": "DEVELOPMENT_FIT_ONLY",
        "not_face_validation": True,
        "n": len(rows),
        "confusion": {
            "S0": _confusion_layer(s0_p),
            "S1_pass": _confusion_layer(s1_p),
            "S1_state_correspondence_n": s1_ok,
            "S2": _confusion_layer(s2_p),
            "S3": _confusion_layer(s3_p),
            "S4": _confusion_layer(s4_p),
            "E1": _confusion_layer(e1_p),
        },
        "human_opening_states": dict(Counter(str(r.get("human_opening_state")) for r in rows)),
        "machine_opening_states": dict(Counter(str(r.get("machine_opening_state")) for r in rows)),
        "clear_exemplars": pos_map,
        "clear_exemplar_s4_n": sum(1 for r in pos_map if r.get("machine_S4")),
        "specials": specials,
        "required_negatives": negatives,
        "curated_negative_n": len(CURATED_NEGATIVES),
        "rows": rows,
        "3382_20241004": _pick("3382", "20241004"),
        "6787_20250214": _pick("6787", "20250214"),
        "4063_20251118": _pick("4063", "20251118"),
    }
