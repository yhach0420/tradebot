"""Corrected candidate freeze. Must not reuse the invalid empty-list SHA."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_corrected_rerun import EXPECTED_PRECOMMIT_SHA256, INVALID_RUN_CANDIDATE_SHA256


def freeze_corrected(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    cands = []
    for r in sorted(rows, key=lambda x: (str(x["target_scope"]), int(x["fx_lookback"]), int(x["response_horizon"]))):
        cands.append(
            {
                "target_scope": r["target_scope"],
                "fx_lookback": int(r["fx_lookback"]),
                "response_horizon": int(r["response_horizon"]),
                "direction": r["direction"],
                "DEV_b_fx": float(r["b_fx"]),
                "DEV_CI": [float(r["ci_lo"]), float(r["ci_hi"])],
                "DEV_q_value": float(r["q"]),
                "D1": bool(r["D1"]),
                "D2": bool(r["D2"]),
                "D3": bool(r["D3"]),
                "D4": bool(r["D4"]),
                "D5": bool(r["D5"]),
                "D6": bool(r["D6"]),
                "DEV_quintile_boundaries": r["quintile_boundaries"],
            }
        )
    payload = {
        "response_contract": "last_completed_close_available_at_le_T",
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "candidates": cands,
    }
    sha = sha256_obj(payload)
    if sha == INVALID_RUN_CANDIDATE_SHA256:
        raise RuntimeError("corrected_freeze_reused_invalid_empty_sha")
    return payload, sha
