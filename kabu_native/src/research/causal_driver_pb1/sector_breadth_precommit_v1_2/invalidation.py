"""Classify the published V1 discovery as technically invalid. Do not overwrite it. No C1 payload."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_bytes
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import (
    AFFECTED_CONSTITUENT_N,
    AFFECTED_SECTOR_ID,
    AFFECTED_SECTOR_NAME,
    AFFECTED_TEST_N,
    INVALIDATION_REASON,
    INVALIDATION_VERDICT,
    N_UNIVERSE,
    OLD_DISCOVERY_CLASSIFICATION,
    OLD_MKT_EX_MIN_N,
    OLD_PUBLISHED_NEXT,
    OLD_PUBLISHED_VERDICT,
    SUPERSEDES_PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.isolation import DISC_OUT


def classify_old_discovery() -> dict[str, Any]:
    path = DISC_OUT / "report.json"
    raw = path.read_bytes() if path.is_file() else b""
    doc = json.loads(raw.decode("utf-8")) if raw else {}
    a = doc.get("answers") or {}
    ev = doc.get("evaluation") or {}
    family = list(ev.get("family") or [])
    affected = [r for r in family if str(r.get("scope_id") or "") == f"SECTOR_{AFFECTED_SECTOR_ID}"]
    rows = []
    n_zero = 0
    driver_positive = 0
    for r in affected:
        model_n = r.get("n")
        qn = ((r.get("quintile_boundaries") or {}).get("n"))
        zero = model_n == 0 or model_n is None
        if zero:
            n_zero += 1
        if qn is not None and int(qn) > 0:
            driver_positive += 1
        rows.append(
            {
                "test_id": r.get("test_id"),
                "metric": r.get("metric"),
                "lookback": r.get("lookback"),
                "horizon": r.get("horizon"),
                "model_n": model_n,
                "ok": r.get("ok"),
                "failed_at": r.get("failed_at"),
                "driver_quintile_n": qn,
                "structurally_unevaluable": bool(zero),
                "driver_side_data_exist": bool(qn is not None and int(qn) > 0),
                "not_genuine_D1_failure": bool(zero),
            }
        )
    max_ex = int(N_UNIVERSE) - int(AFFECTED_CONSTITUENT_N)
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
        "old_precommit_sha256": a.get("precommit_sha256") or SUPERSEDES_PRECOMMIT_SHA256,
        "C1_opened": bool(a.get("C1_opened")),
        "C1_rows_read_before_candidate_freeze": int(a.get("C1_rows_read_before_candidate_freeze") or 0),
        "DEV_all_n": a.get("DEV_all_n"),
        "DEV_candidate_n": a.get("DEV_candidate_n"),
        "DEV_first_fail_counts": a.get("DEV_first_fail_counts"),
        "DEV_first_fail_counts_status": "INVALID_FOR_FINAL_DECISION",
        "BH_m_used": 384,
        "BH_q_and_D3_status": "INVALID_FOR_FINAL_DECISION",
        "candidate_n_zero_status": "INVALID_FOR_FINAL_DECISION",
        "do_not_set_m_to_352": True,
        "family_remains_384": True,
        "affected_sector_id": AFFECTED_SECTOR_ID,
        "affected_sector_name": AFFECTED_SECTOR_NAME,
        "affected_constituent_n": AFFECTED_CONSTITUENT_N,
        "n_universe": N_UNIVERSE,
        "old_max_ex_sector_n": max_ex,
        "old_required_n": OLD_MKT_EX_MIN_N,
        "old_gate_structurally_possible": max_ex >= OLD_MKT_EX_MIN_N,
        "affected_hypothesis_n": AFFECTED_TEST_N,
        "affected_rows_found": len(affected),
        "affected_model_n_zero": n_zero,
        "affected_driver_quintile_n_positive": driver_positive,
        "all_32_model_n_zero": bool(len(affected) == AFFECTED_TEST_N and n_zero == AFFECTED_TEST_N),
        "control_gate_impossibility_not_missing_driver": bool(n_zero == AFFECTED_TEST_N and driver_positive == AFFECTED_TEST_N),
        "affected_tests": rows,
        "near_misses_audit_only": True,
        "near_misses_not_used_to_modify_contract": True,
        "C1_outcomes_opened": False,
    }
