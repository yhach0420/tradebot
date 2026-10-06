"""FAILED_OPEN and Family A controls. Do not redesign if unchanged."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v2_rca.reconstruct import pick

FO_POS = (("3382", "20241004"), ("7011", "20250523"), ("4063", "20251118"))
FO_NEG = (("3382", "20241115"), ("6273", "20250120"), ("5802", "20250613"), ("7182", "20251112"))
FAM = (("9432", "20250402"), ("8058", "20250814"))


def _line(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    fail = dict(snap.get("failed_open_v2") or {})
    loc = dict(snap.get("location_at_0914_v2") or {})
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "human_location": row.get("human_location"),
        "machine_SEED": row.get("machine_SEED"),
        "machine_opening_state": row.get("machine_opening_state"),
        "machine_ACTIVE": row.get("machine_ACTIVE"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "failed_open_form": row.get("failed_open_form") or fail.get("form"),
        "failed_open_ok": fail.get("ok"),
        "true_first": (snap.get("seed_row_v2") or {}).get("true_first"),
        "location_family": row.get("location_family"),
        "location_A_class": row.get("location_A_class"),
        "location_id": row.get("location_id"),
        "e1_entry_t": row.get("e1_entry_t"),
        "identify_0914_A_class": loc.get("A_class"),
    }


def controls(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [_line(pick(rows, s, d)) for s, d in FO_POS]
    neg = [_line(pick(rows, s, d)) for s, d in FO_NEG]
    fam = [_line(pick(rows, s, d)) for s, d in FAM]
    pos_ok = all(str(p.get("machine_SEED") or "") == "FAILED_OPEN_SEED" and p.get("true_first") is not True for p in pos)
    neg_ok = all(str(n.get("machine_SEED") or "") != "FAILED_OPEN_SEED" and not n.get("machine_THESIS_READY") for n in neg)
    fam_ok = all(
        str(f.get("location_A_class") or "").startswith("A2") and "VWAP" not in str(f.get("location_id") or "") for f in fam
    )
    return {
        "positives": pos,
        "negatives": neg,
        "family_a_focus": fam,
        "FAILED_OPEN_semantics_still_hold": pos_ok and neg_ok,
        "Family_A_semantics_still_hold": fam_ok,
        "redesign_failed_open": False,
        "redesign_family_a": False,
        "3382_20241004": pos[0],
        "7011_20250523": pos[1],
        "4063_20251118": pos[2],
        "3382_20241115": neg[0],
        "6273_20250120": neg[1],
        "5802_20250613": neg[2],
        "7182_20251112": neg[3],
        "9432_20250402": fam[0],
        "8058_20250814": fam[1],
    }
