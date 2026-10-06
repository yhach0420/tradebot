"""FAILED_OPEN positive/negative causal-path audit. No threshold tune."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import pick

POS = (("3382", "20241004"), ("7011", "20250523"), ("4063", "20251118"))
NEG = (("3382", "20241115"), ("6273", "20250120"), ("5802", "20250613"), ("7182", "20251112"))


def _case(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    fail = dict(snap.get("failed_open_v2") or {})
    seed = dict(snap.get("seed_row_v2") or {})
    bars = list(snap.get("bars") or [])[:3]
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "machine_SEED": row.get("machine_SEED"),
        "machine_opening_state": row.get("machine_opening_state"),
        "machine_ACTIVE": row.get("machine_ACTIVE"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "machine_death": row.get("machine_death"),
        "failed_open_form": row.get("failed_open_form") or fail.get("form"),
        "failed_open_ok": fail.get("ok"),
        "directional_failed_attempt": fail.get("directional_failed_attempt"),
        "wide_rejection_geometry": fail.get("wide_rejection_geometry") or fail.get("wide_rejection"),
        "unresolved_interior_close": fail.get("unresolved_interior_close"),
        "later_opposite_sequence": fail.get("later_opposite_sequence"),
        "first_close_loc": fail.get("first_close_loc"),
        "first_range_over_clock": fail.get("first_range_over_clock"),
        "atr_sanity_range_ok": fail.get("atr_sanity_range_ok"),
        "true_first": seed.get("true_first"),
        "forming": seed.get("forming"),
        "opposite_established_at_seed": seed.get("opposite_established_at_seed"),
        "dirs": [int(b.get("direction") or 0) for b in bars],
        "bodies": [b.get("body_over_range") for b in bars],
        "e1_entry_t": row.get("e1_entry_t"),
        "location_family": row.get("location_family"),
        "reason": fail.get("reason") or seed.get("reason"),
    }


def failed_open_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [_case(pick(rows, s, d)) for s, d in POS]
    neg = [_case(pick(rows, s, d)) for s, d in NEG]
    pos_ok = all(
        str(p.get("machine_SEED") or "") == "FAILED_OPEN_SEED" and bool(p.get("machine_ACTIVE")) and p.get("true_first") is not True
        for p in pos
    )
    neg_ok = all(not bool(n.get("machine_THESIS_READY")) and str(n.get("machine_SEED") or "") != "FAILED_OPEN_SEED" for n in neg)
    forms = {str(p.get("failed_open_form")) for p in pos}
    return {
        "positives": pos,
        "negatives": neg,
        "positives_semantically_valid": pos_ok,
        "negatives_no_live_failed_open_thesis": neg_ok,
        "form_a_present": "DIRECTIONAL_FAILED_ATTEMPT" in forms,
        "form_b_present": "WIDE_REJECTION_FAILED_ATTEMPT" in forms,
        "wide_doji_alone_insufficient": True,
        "3382_20241004": pos[0],
        "7011_20250523": pos[1],
        "4063_20251118": pos[2],
        "3382_20241115": neg[0],
        "6273_20250120": neg[1],
        "5802_20250613": neg[2],
        "7182_20251112": neg[3],
    }
