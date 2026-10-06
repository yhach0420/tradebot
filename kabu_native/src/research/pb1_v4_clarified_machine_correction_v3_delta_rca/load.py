"""Read-only load of V3 walk, 88 snaps, and V3 report. Does not re-walk Discovery."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_correction_v3_delta_rca.isolation import FACE_RCA_CACHE, PARITY_RCA_CACHE, V3_CACHE, V3_OUT


def load_walked() -> dict[str, Any]:
    path = V3_CACHE / "walked.json"
    if not path.is_file():
        return {"ok": False}
    return json.loads(path.read_text(encoding="utf-8"))


def load_v3_report() -> dict[str, Any]:
    path = V3_OUT / "report.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_materialized() -> dict[tuple[str, str], dict[str, Any]]:
    path = PARITY_RCA_CACHE / "materialized_88.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in list(payload.get("rows") or []):
        out[(str(r.get("symbol")), str(r.get("date")))] = dict(r.get("snap") or r)
    return out


def load_rca_rows() -> list[dict[str, Any]]:
    path = FACE_RCA_CACHE / "descriptor_slim.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def funnel_idx(funnel: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in funnel:
        out[(str(r.get("symbol")), str(r.get("date")))] = r
    return out


def pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    for r in rows:
        if str(r.get("symbol")) == symbol and str(r.get("date")) == date:
            return dict(r)
    return {"symbol": symbol, "date": date, "missing": True}


def join_88(*, walked: dict[str, Any]) -> list[dict[str, Any]]:
    rca = load_rca_rows()
    fidx = funnel_idx(list(walked.get("funnel_days") or []))
    e0idx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("e0_events") or [])}
    e1idx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("e1_events") or [])}
    snaps = load_materialized()
    rows = []
    for r in rca:
        rid = int(r.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        key = (str(r.get("symbol")), str(r.get("date")))
        f = dict(fidx.get(key) or {})
        rows.append(
            {
                "rca_id": rid,
                "symbol": key[0],
                "date": key[1],
                "human_opening_state": lab.get("sp_opening_state"),
                "human_pattern": lab.get("sp_pattern"),
                "human_late": bool(lab.get("sp_late")),
                "machine_SEED": f.get("OPENING_DRIVE_SEED"),
                "machine_opening_state": f.get("opening_state"),
                "machine_DIR": f.get("DIR"),
                "machine_WHY": f.get("WHY_THIS_STOCK"),
                "machine_ACTIVE_LIVE": f.get("OPENING_DRIVE_LIVE"),
                "machine_ACTIVE_REACHED": f.get("OPENING_DRIVE_REACHED"),
                "machine_THESIS_LIVE": f.get("THESIS_LIVE"),
                "machine_THESIS_REACHED": f.get("THESIS_REACHED"),
                "machine_THESIS_LOST": f.get("THESIS_LOST"),
                "machine_THESIS_LOST_AT": f.get("THESIS_LOST_AT"),
                "machine_THESIS_LOST_REASON": f.get("THESIS_LOST_REASON"),
                "machine_LOCATION": f.get("LOCATION_IDENTIFIED"),
                "post_dominant_class_funnel": f.get("post_dominant_class"),
                "sandwich_semantic_class_funnel": f.get("sandwich_semantic_class"),
                "last_progress_class": f.get("last_progress_class"),
                "progress_log": f.get("progress_log"),
                "death": f.get("death"),
                "or_high": f.get("or_high"),
                "or_low": f.get("or_low"),
                "e0_entry_t": (e0idx.get(key) or {}).get("entry_t"),
                "e1_entry_t": (e1idx.get(key) or {}).get("entry_t"),
                "snap": snaps.get(key) or {},
                "funnel": f,
            }
        )
    return rows
