"""Freeze the exact 8 C1-confirmed candidates from the V2 corrected discovery. No reselection."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import (
    CANDIDATE_LIST_SHA256,
    EXPECTED_C1_CONFIRMED_N,
    EXPECTED_DEV_CANDIDATE_N,
    PARENT_PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.isolation import DISC_V2_OUT
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import overlap_proof_for_candidate


IDENTITY_FIELDS = (
    "test_id",
    "metric",
    "scope_id",
    "sector_id",
    "lookback",
    "horizon",
    "direction",
    "global",
    "mechanism",
    "DEV_beta_primary",
    "DEV_CI",
    "DEV_p",
    "DEV_q_value",
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D7",
    "C1",
    "C2",
    "C3",
    "C4",
    "C5",
    "C6",
    "C7",
    "c1_confirmed",
)


def load_v2_discovery() -> dict[str, Any]:
    path = DISC_V2_OUT / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sort_key(row: dict[str, Any]) -> tuple:
    return (str(row.get("metric")), str(row.get("scope_id")), int(row.get("lookback") or 0), int(row.get("horizon") or 0))


def canonical_identity(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in IDENTITY_FIELDS}
    out["c1_confirmed"] = True
    return out


def bind_c1_confirmed_set() -> dict[str, Any]:
    blockers: list[str] = []
    doc = load_v2_discovery()
    a = doc.get("answers") or {}
    ev = doc.get("evaluation") or {}
    if a.get("candidate_list_sha256") != CANDIDATE_LIST_SHA256:
        blockers.append("CANDIDATE_LIST_SHA_MISMATCH")
    if a.get("precommit_sha256") != PARENT_PRECOMMIT_SHA256:
        blockers.append("PARENT_PRECOMMIT_SHA_MISMATCH")
    if int(a.get("DEV_candidate_n") or 0) != EXPECTED_DEV_CANDIDATE_N:
        blockers.append("DEV_CANDIDATE_N_MISMATCH")
    if int(a.get("C1_confirmed_n") or 0) != EXPECTED_C1_CONFIRMED_N:
        blockers.append("C1_CONFIRMED_N_MISMATCH")
    if int(a.get("C1_rows_read_before_corrected_candidate_freeze") or a.get("C1_rows_read_before_candidate_freeze") or 0) != 0:
        blockers.append("C1_ROWS_BEFORE_FREEZE_NOT_ZERO")
    frozen = list(ev.get("frozen_candidates") or a.get("frozen_candidates") or [])
    if len(frozen) != EXPECTED_DEV_CANDIDATE_N:
        blockers.append("FROZEN_CANDIDATE_N_MISMATCH")
    c1_rows = list(ev.get("c1_rows") or [])
    confirmed = [r for r in c1_rows if r.get("c1_confirmed")]
    if len(confirmed) != EXPECTED_C1_CONFIRMED_N:
        blockers.append("C1_CONFIRMED_ROW_N_MISMATCH")
    frozen_ids = {str(r.get("test_id")) for r in frozen}
    for r in confirmed:
        if str(r.get("test_id")) not in frozen_ids:
            blockers.append("CONFIRMED_NOT_IN_FROZEN_SET")
            break
        if not all(bool(r.get(k)) for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C7")):
            blockers.append("CONFIRMED_STATUS_CHANGED")
            break
    by_id = {str(r.get("test_id")): r for r in frozen}
    records = []
    for r in sorted(confirmed, key=_sort_key):
        base = dict(by_id.get(str(r.get("test_id")) or "") or {})
        merged = {
            **canonical_identity(base),
            "C1": bool(r.get("C1")),
            "C2": bool(r.get("C2")),
            "C3": bool(r.get("C3")),
            "C4": bool(r.get("C4")),
            "C5": bool(r.get("C5")),
            "C6": bool(r.get("C6")),
            "C7": bool(r.get("C7")),
            "c1_confirmed": True,
            "metric": r.get("metric"),
            "scope_id": r.get("scope_id"),
            "sector_id": r.get("sector_id"),
            "lookback": int(r["lookback"]),
            "horizon": int(r["horizon"]),
            "direction": r.get("direction") or base.get("direction"),
            "global": bool(r.get("global")),
            "mechanism": r.get("mechanism") or base.get("mechanism"),
            "test_id": r.get("test_id"),
            "DEV_beta_primary": base.get("DEV_beta_primary"),
            "DEV_CI": base.get("DEV_CI"),
            "DEV_p": base.get("DEV_p"),
            "DEV_q_value": base.get("DEV_q_value"),
            "D1": base.get("D1"),
            "D2": base.get("D2"),
            "D3": base.get("D3"),
            "D4": base.get("D4"),
            "D5": base.get("D5"),
            "D6": base.get("D6"),
            "D7": base.get("D7"),
        }
        merged["offset_def"] = overlap_proof_for_candidate(lookback=int(merged["lookback"]), horizon=int(merged["horizon"]))
        records.append(merged)
    payload = {
        "namespace": "SECTOR_BREADTH_DISPERSION_C1_CONFIRMED_SET_V1_3",
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "n": len(records),
        "candidates": [canonical_identity(r) for r in records],
    }
    digest = sha256_obj(payload)
    return {
        "pass": not blockers and len(records) == EXPECTED_C1_CONFIRMED_N,
        "blockers": blockers,
        "n": len(records),
        "records": records,
        "c1_confirmed_set_sha256": digest,
        "candidate_list_sha256": CANDIDATE_LIST_SHA256,
        "payload": payload,
        "DEV_candidate_n": int(a.get("DEV_candidate_n") or 0),
        "C1_confirmed_n": len(records),
        "C1_rows_read_before_corrected_candidate_freeze": int(
            a.get("C1_rows_read_before_corrected_candidate_freeze") or a.get("C1_rows_read_before_candidate_freeze") or 0
        ),
        "reason_raw": a.get("reason"),
        "old_verdict": a.get("VERDICT"),
        "old_next": a.get("NEXT"),
        "all_384_evaluable": a.get("all_384_evaluable"),
        "unaffected_parity_fail_n": a.get("unaffected_parity_fail_n"),
        "affected_3650_model_n_zero_after": a.get("affected_3650_model_n_zero_after"),
    }
