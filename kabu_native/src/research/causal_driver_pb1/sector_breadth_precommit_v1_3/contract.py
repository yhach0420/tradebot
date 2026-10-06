"""V1.3 contract: frozen 8-candidate non-overlapping offset placebo. No corrected betas."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import (
    CANDIDATE_LIST_SHA256,
    CAUSAL_OFFSETS,
    DECISION_STATUS,
    FAIL_NEXT_IF_ALL_OFFSET_FAIL,
    FAIL_REASON_IF_ALL_OFFSET_FAIL,
    FAIL_VERDICT_IF_ALL_OFFSET_FAIL,
    OFFSET_ABS_RATIO,
    PARENT_PRECOMMIT_SHA256,
    PASS_NEXT,
    PASS_VERDICT,
    PRECOMMIT_ID,
    SESSION_CLOSE,
    SESSION_OPEN,
)


def frozen_contract_v1_3(
    *,
    c1_confirmed_set_sha256: str,
    candidates: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    overlap_ok: bool,
) -> dict[str, Any]:
    sample_by_id = {str(r.get("test_id")): r for r in samples}
    cand_rows = []
    for rec in candidates:
        tid = str(rec.get("test_id"))
        proof = rec.get("offset_def") or {}
        samp = sample_by_id.get(tid) or {}
        cand_rows.append(
            {
                "test_id": tid,
                "metric": rec.get("metric"),
                "scope_id": rec.get("scope_id"),
                "sector_id": rec.get("sector_id"),
                "lookback": rec.get("lookback"),
                "horizon": rec.get("horizon"),
                "direction": rec.get("direction"),
                "K1": proof.get("K1"),
                "K2": proof.get("K2"),
                "K3": proof.get("K3"),
                "all_future_overlap_zero": proof.get("all_future_overlap_zero"),
                "session_feasible": proof.get("session_feasible"),
                "offset_common_sample_n": samp.get("offset_common_sample_n"),
                "offset_common_date_n": samp.get("offset_common_date_n"),
                "offset_common_sample_sha256": samp.get("offset_common_sample_sha256"),
            }
        )
    base: dict[str, Any] = {
        "precommit_id": PRECOMMIT_ID,
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "c1_confirmed_set_sha256": c1_confirmed_set_sha256,
        "task": "POST_C1_NONOVERLAPPING_OFFSET_PLACEBO_ONLY",
        "no_dev_c1_reselection": True,
        "no_bh": True,
        "no_384_scan": True,
        "no_corrected_offset_beta_in_this_artifact": True,
        "decision_status": DECISION_STATUS,
        "driver_shift_only": True,
        "target_remains_T_to_T_plus_h": True,
        "controls_remain_at_T": True,
        "causal_offsets": list(CAUSAL_OFFSETS),
        "future_offsets": "K1=h+w+1; K2=h+w+3; K3=h+w+5",
        "common_sample": "row valid iff finite for all -5,-3,-1,0,K1,K2,K3 under same date,T,target,controls,freshness,PIT listing",
        "session": {"open": SESSION_OPEN, "close": SESSION_CLOSE, "future_driver_end_le_close": True, "past_windows_ge_open": True},
        "gates": {
            "O1": "offset 0 retains DEV direction",
            "O2": "at least one of -1,-3 same direction",
            "O3": f"abs(beta_0) >= {OFFSET_ABS_RATIO} * max(abs(beta_K1), abs(beta_K2), abs(beta_K3))",
            "O4": "all future comparison windows overlap_minutes=0",
            "O3_fail_label": "FOLLOWER_OR_CONTEMPORANEOUS_NOT_LEAD",
            "threshold_not_optimized": True,
            "threshold": float(OFFSET_ABS_RATIO),
        },
        "downstream_if_offset_pass": ("day_shuffle", "sector_identity", "driver_concentration", "target_concentration", "common_factor"),
        "fail_all_offset": {
            "VERDICT": FAIL_VERDICT_IF_ALL_OFFSET_FAIL,
            "reason": FAIL_REASON_IF_ALL_OFFSET_FAIL,
            "NEXT": FAIL_NEXT_IF_ALL_OFFSET_FAIL,
        },
        "pass_full_chain": {"VERDICT": PASS_VERDICT, "NEXT": PASS_NEXT, "ALPHA_CREATED": False},
        "overlap_ok": bool(overlap_ok),
        "candidates": cand_rows,
    }
    base["precommit_sha256"] = sha256_obj(base)
    return base
