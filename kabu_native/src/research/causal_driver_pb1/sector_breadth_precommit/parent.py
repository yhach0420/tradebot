"""Bind parent leader-laggard closeout. Do not overwrite parent artifacts. No rerun."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.sector_breadth_precommit import (
    PARENT_C1_CONFIRMED_N,
    PARENT_CANDIDATE_LIST_SHA256,
    PARENT_DECISION_REASON,
    PARENT_DEV_CANDIDATE_N,
    PARENT_FINAL_PASS_N,
    PARENT_PRECOMMIT_SHA256,
    PARENT_REPORT_REASON_RAW,
    PARENT_STATUS,
    PARENT_VERDICT,
)
from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.sector_breadth_precommit.isolation import LL_DISC_OUT


def bind_parent() -> dict[str, Any]:
    blockers: list[str] = []
    path = LL_DISC_OUT / "report.json"
    raw = path.read_bytes() if path.is_file() else b""
    doc = json.loads(raw.decode("utf-8")) if raw else {}
    a = doc.get("answers") or {}
    if a.get("VERDICT") != PARENT_VERDICT:
        blockers.append("PARENT_VERDICT_MISMATCH")
    if a.get("candidate_list_sha256") != PARENT_CANDIDATE_LIST_SHA256:
        blockers.append("PARENT_CANDIDATE_SHA_MISMATCH")
    if a.get("DEV_candidate_n") != PARENT_DEV_CANDIDATE_N:
        blockers.append("PARENT_DEV_CANDIDATE_N_MISMATCH")
    if a.get("C1_confirmed_n") != PARENT_C1_CONFIRMED_N:
        blockers.append("PARENT_C1_CONFIRMED_N_MISMATCH")
    if a.get("final_pass_n") != PARENT_FINAL_PASS_N:
        blockers.append("PARENT_FINAL_PASS_N_MISMATCH")
    if a.get("precommit_sha256") != PARENT_PRECOMMIT_SHA256:
        blockers.append("PARENT_PRECOMMIT_SHA_MISMATCH")
    if a.get("reason") != PARENT_REPORT_REASON_RAW:
        blockers.append("PARENT_RAW_REASON_MISMATCH")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "VERDICT": a.get("VERDICT"),
        "parent_report_reason_raw": a.get("reason"),
        "parent_decision_reason": PARENT_DECISION_REASON,
        "parent_decision_reason_detail": "9 / 9 C1-confirmed candidates failed as FOLLOWER_OR_CONTEMPORANEOUS_NOT_LEAD",
        "CROSS_SECTIONAL_LEADER_LAGGARD_STATUS": PARENT_STATUS,
        "candidate_list_sha256": a.get("candidate_list_sha256"),
        "DEV_candidate_n": a.get("DEV_candidate_n"),
        "C1_confirmed_n": a.get("C1_confirmed_n"),
        "final_pass_n": a.get("final_pass_n"),
        "precommit_sha256": a.get("precommit_sha256"),
        "offset_pass_n": a.get("offset_pass_n"),
        "parent_report_file_sha256": sha256_bytes(raw) if raw else None,
        "parent_artifacts_overwritten": False,
        "statistical_rerun": False,
        "leaders_changed": False,
        "lookbacks_changed": False,
        "horizons_changed": False,
        "offset_gate_changed": False,
        "c1_near_misses_reused": False,
        "LEADER_LAGGARD_REOPENED": False,
    }
