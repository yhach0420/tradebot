"""Join 88 + attach RCA snaps + reclassify seed with frozen Correction V2 (read-only)."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_correction_v2.location import identify_location
from research.pb1_v4_clarified_machine_correction_v2.seed import classify_seed, continued_intent_state, failed_open_seed_visible
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.isolation import (
    CORRECTION_CACHE,
    CORRECTION_OUT,
    FACE_RCA_CACHE,
    PARITY_RCA_CACHE,
)


def loc_time(location_id: Any) -> str | None:
    if not location_id:
        return None
    parts = str(location_id).split("|")
    for p in reversed(parts):
        if len(p) == 5 and p[2] == ":":
            return p
    return None


def load_walked() -> dict[str, Any]:
    path = CORRECTION_CACHE / "walked.json"
    if not path.is_file():
        return {"ok": False}
    return json.loads(path.read_text(encoding="utf-8"))


def load_changed_manifest() -> dict[str, Any]:
    path = CORRECTION_OUT / "changed_row_manifest.json"
    if not path.is_file():
        return {"changed_n": 0, "rows": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_rca_rows() -> list[dict[str, Any]]:
    path = FACE_RCA_CACHE / "descriptor_slim.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_materialized_snaps() -> dict[tuple[str, str], dict[str, Any]]:
    path = PARITY_RCA_CACHE / "materialized_88.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in list(payload.get("rows") or []):
        out[(str(r.get("symbol")), str(r.get("date")))] = dict(r.get("snap") or {})
    return out


def funnel_idx(funnel: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in funnel:
        out[(str(r.get("symbol")), str(r.get("date")))] = r
    return out


def join_88(*, walked: dict[str, Any]) -> list[dict[str, Any]]:
    rca = load_rca_rows()
    fidx = funnel_idx(list(walked.get("funnel_days") or []))
    e0idx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("e0_events") or [])}
    e1idx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("e1_events") or [])}
    sidx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("setups") or [])}
    rows = []
    for r in rca:
        rid = int(r.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        key = (str(r.get("symbol")), str(r.get("date")))
        f = dict(fidx.get(key) or {})
        setup = sidx.get(key) or {}
        rows.append(
            {
                "rca_id": rid,
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "direction_v32": r.get("direction"),
                "human_pattern": lab.get("sp_pattern"),
                "human_opening_state": lab.get("sp_opening_state"),
                "human_location": lab.get("sp_location"),
                "human_retest": lab.get("sp_retest"),
                "human_trigger": lab.get("sp_trigger"),
                "human_late": bool(lab.get("sp_late")),
                "human_note": lab.get("sp_note"),
                "machine_WHY": bool(f.get("WHY_THIS_STOCK")),
                "machine_SEED": f.get("OPENING_DRIVE_SEED"),
                "machine_ACTIVE": bool(f.get("OPENING_DRIVE_ACTIVE")),
                "machine_LOCATION": bool(f.get("LOCATION_IDENTIFIED")),
                "machine_THESIS_READY": bool(f.get("THESIS_READY")),
                "machine_E0": bool(f.get("E0")),
                "machine_E1": bool(f.get("E1")),
                "machine_opening_state": f.get("opening_state"),
                "machine_DIR": f.get("DIR"),
                "machine_death": f.get("death"),
                "location_family": f.get("location_family") or setup.get("location_family"),
                "location_A_class": f.get("location_A_class") or setup.get("location_A_class"),
                "location_reason": f.get("location_reason") or setup.get("location_reason"),
                "failed_open_form": f.get("failed_open_form") or setup.get("failed_open_form"),
                "family_a_seen": f.get("family_a_seen") or setup.get("family_a_seen"),
                "progress_log": f.get("progress_log") or setup.get("progress_log"),
                "last_progress_class": f.get("last_progress_class"),
                "interaction": f.get("interaction") or setup.get("interaction"),
                "opening_drive_id": f.get("opening_drive_id") or setup.get("opening_drive_id"),
                "location_id": f.get("location_id") or setup.get("location_id"),
                "thesis_id": f.get("thesis_id") or setup.get("thesis_id"),
                "location_t": loc_time(f.get("location_id") or setup.get("location_id")),
                "hidden_1m_snapshot": setup.get("hidden_1m_snapshot") or f.get("hidden_1m_snapshot"),
                "e0_entry_t": (e0idx.get(key) or {}).get("entry_t"),
                "e1_entry_t": (e1idx.get(key) or {}).get("entry_t"),
                "or_high": f.get("or_high") or setup.get("or_high"),
                "or_low": f.get("or_low") or setup.get("or_low"),
            }
        )
    return rows


def attach_snaps_and_reclassify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reuse RCA 5m bars/zones. Re-run Correction V2 seed/location classifiers read-only."""
    snaps = load_materialized_snaps()
    out = []
    for r in rows:
        key = (str(r.get("symbol")), str(r.get("date")))
        snap = dict(snaps.get(key) or {})
        bars = list(snap.get("bars") or [])
        clock = dict(snap.get("clock_snap") or {})
        atr = snap.get("atr20")
        seed_now = None
        fail_now = None
        intent = None
        loc_0914 = None
        if len(bars) >= 3:
            seed_now = classify_seed(bars[:3], clock_snap=clock, atr20=atr)
            fail_now = failed_open_seed_visible(bars[:3], list(clock.get("same_clock") or []), atr20=atr)
            sign = int(seed_now.get("DIR") or r.get("machine_DIR") or 0)
            if sign in (1, -1):
                intent = continued_intent_state(bars[:3], sign=sign)
            if sign in (1, -1) and snap.get("or_high") is not None and snap.get("or_low") is not None:
                loc_0914 = identify_location(
                    sign=sign,
                    active=True,
                    bar=bars[2],
                    or_high=float(snap["or_high"]),
                    or_low=float(snap["or_low"]),
                    zones=list(snap.get("zones") or []),
                    pdh=snap.get("pdh"),
                    pdl=snap.get("pdl"),
                    pdc=snap.get("pdc"),
                    vwap=snap.get("vwap_open"),
                    n1m=None,
                    left=False,
                    session_open=snap.get("open"),
                )
        out.append(
            {
                **r,
                "snap": {
                    **snap,
                    "seed_row_v2": seed_now,
                    "failed_open_v2": fail_now,
                    "intent_v2": intent,
                    "location_at_0914_v2": loc_0914,
                },
            }
        )
    return out


def pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    return next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), {}) or {}
