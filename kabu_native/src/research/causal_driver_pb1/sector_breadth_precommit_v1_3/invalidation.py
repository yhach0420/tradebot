"""Classify published V2 as technically invalid due to overlapping future offsets. Do not overwrite it."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import (
    DECISION_STATUS,
    INVALIDATION_REASON,
    INVALIDATION_VERDICT,
    OLD_DISCOVERY_CLASSIFICATION,
    OLD_FUTURE_OFFSETS,
    OLD_PUBLISHED_NEXT,
    OLD_PUBLISHED_VERDICT,
    REASON_RAW,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.candidates import bind_c1_confirmed_set, load_v2_discovery
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.isolation import DISC_V2_OUT
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import old_future_overlap_minutes


def classify_v2() -> dict[str, Any]:
    path = DISC_V2_OUT / "report.json"
    raw = path.read_bytes() if path.is_file() else b""
    doc = load_v2_discovery()
    a = doc.get("answers") or {}
    bound = bind_c1_confirmed_set()
    old_overlap = []
    for rec in bound.get("records") or []:
        w = int(rec["lookback"])
        h = int(rec["horizon"])
        for k in OLD_FUTURE_OFFSETS:
            ov = old_future_overlap_minutes(lookback=w, horizon=h, k=int(k))
            old_overlap.append(
                {
                    "test_id": rec.get("test_id"),
                    "lookback": w,
                    "horizon": h,
                    "k": int(k),
                    "overlap_minutes": ov,
                    "classification": "INVALID_FOR_LEAD_LAG_DECISION_DUE_TO_RETURN_WINDOW_OVERLAP",
                }
            )
    return {
        "INVALIDATION_VERDICT": INVALIDATION_VERDICT,
        "reason": INVALIDATION_REASON,
        "old_discovery_classification": OLD_DISCOVERY_CLASSIFICATION,
        "old_published_verdict": a.get("VERDICT") or OLD_PUBLISHED_VERDICT,
        "old_published_next": a.get("NEXT") or OLD_PUBLISHED_NEXT,
        "old_published_next_suspended": True,
        "do_not_use_for_driver_family_decision": True,
        "artifacts_preserved": True,
        "discovery_path": str(path),
        "discovery_report_file_sha256": sha256_bytes(raw) if raw else None,
        "reason_raw": a.get("reason") or REASON_RAW,
        "decision_status": DECISION_STATUS,
        "C1_confirmed_n": bound.get("C1_confirmed_n"),
        "DEV_candidate_n": bound.get("DEV_candidate_n"),
        "all_384_evaluable_remains_valid": True,
        "structural_3650_correction_remains_valid": True,
        "unaffected_parity_remains_valid": True,
        "D1_D7_remain_valid": True,
        "C1_C7_remain_valid": True,
        "old_offset_pass_n_not_decision_valid": True,
        "old_future_offsets": list(OLD_FUTURE_OFFSETS),
        "old_future_offset_rows": old_overlap,
        "do_not_reuse_old_positive_offset_magnitudes": True,
    }
