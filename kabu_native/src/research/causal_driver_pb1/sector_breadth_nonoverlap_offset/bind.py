"""Fail-closed bind to precommit V1.3 and the frozen 8-candidate common samples."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.phase2_precommit_v1_1.shuffle import generate_shuffle_permutations
from research.causal_driver_pb1.sector_breadth_discovery import EXPECTED_C1_N
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset import (
    C1_CONFIRMED_SET_SHA256,
    CANDIDATE_LIST_SHA256,
    COMMON_SAMPLE,
    EXCLUDED_C2_IDS,
    EXPECTED_C1_CONFIRMED_N,
    EXPECTED_PERMUTATION_SHA256,
    EXPECTED_PRECOMMIT_SHA256,
    FROZEN_IDS,
    PARENT_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
)
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.isolation import DISC_V2_OUT, V13_OUT
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.candidates import bind_c1_confirmed_set
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.offset_def import future_placebo_offsets


def _load(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def bind_run() -> dict[str, Any]:
    blockers: list[str] = []
    v13 = _load(V13_OUT / "report.json")
    a13 = v13.get("answers") or {}
    ev13 = v13.get("evaluation") or {}
    contract = ev13.get("contract") or {}
    if a13.get("precommit_id") != PRECOMMIT_ID or ev13.get("precommit_id") != PRECOMMIT_ID:
        blockers.append("PRECOMMIT_ID_MISMATCH")
    if a13.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256 or contract.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA256_MISMATCH")
    if a13.get("parent_precommit_sha256") != PARENT_PRECOMMIT_SHA256:
        blockers.append("PARENT_PRECOMMIT_SHA_MISMATCH")
    if a13.get("candidate_list_sha256") != CANDIDATE_LIST_SHA256:
        blockers.append("CANDIDATE_LIST_SHA_MISMATCH")
    if a13.get("c1_confirmed_set_sha256") != C1_CONFIRMED_SET_SHA256:
        blockers.append("C1_CONFIRMED_SET_SHA_MISMATCH")
    if int(a13.get("C1_confirmed_n") or 0) != EXPECTED_C1_CONFIRMED_N:
        blockers.append("C1_CONFIRMED_N_MISMATCH")
    bound = bind_c1_confirmed_set()
    blockers.extend(list(bound.get("blockers") or []))
    if bound.get("c1_confirmed_set_sha256") != C1_CONFIRMED_SET_SHA256:
        blockers.append("C1_CONFIRMED_SET_RECOMPUTE_MISMATCH")
    records = list(bound.get("records") or [])
    ids = [str(r.get("test_id")) for r in records]
    if tuple(ids) != FROZEN_IDS:
        blockers.append("FROZEN_8_IDENTITY_MISMATCH")
    if any(tid in ids for tid in EXCLUDED_C2_IDS):
        blockers.append("C2_FAILURE_REOPENED")
    for rec in records:
        if str(rec.get("direction")) != "UP":
            blockers.append("DIRECTION_NOT_UP")
            break
        if str(rec.get("metric")) != "BREADTH":
            blockers.append("METRIC_NOT_BREADTH")
            break
        w = int(rec["lookback"])
        h = int(rec["horizon"])
        expect_k = future_placebo_offsets(lookback=w, horizon=h)
        spec = COMMON_SAMPLE.get(str(rec.get("test_id"))) or {}
        got = tuple(int(x) for x in (spec.get("offsets") or ()))
        if got != (-5, -3, -1, 0) + expect_k:
            blockers.append(f"OFFSET_LIST_MISMATCH:{rec.get('test_id')}")
        if 1 in got:
            blockers.append(f"OLD_PLUS1_PRESENT:{rec.get('test_id')}")
    contract_rows = {str(r.get("test_id")): r for r in (contract.get("candidates") or [])}
    for tid, spec in COMMON_SAMPLE.items():
        row = contract_rows.get(tid) or {}
        if int(row.get("offset_common_sample_n") or -1) != int(spec["n"]):
            blockers.append(f"PRECOMMIT_N_MISMATCH:{tid}")
        if str(row.get("offset_common_sample_sha256") or "") != str(spec["sha256"]):
            blockers.append(f"PRECOMMIT_SAMPLE_SHA_MISMATCH:{tid}")
    v2 = _load(DISC_V2_OUT / "report.json")
    ev2 = v2.get("evaluation") or {}
    c1_dates = list((ev2.get("bound") or {}).get("c1_dates") or [])
    dev_dates = list((ev2.get("bound") or {}).get("development_dates") or [])
    eligible = list((ev2.get("bound") or {}).get("eligible_dates") or [])
    if len(c1_dates) != EXPECTED_C1_N:
        blockers.append("C1_DATE_N_MISMATCH")
    shuffle = generate_shuffle_permutations(eligible)
    if shuffle.get("permutation_sha256") != EXPECTED_PERMUTATION_SHA256:
        blockers.append("PERMUTATION_SHA_MISMATCH")
    c1_beta = {}
    for row in ev2.get("c1_rows") or []:
        tid = str(row.get("test_id"))
        if tid in COMMON_SAMPLE and row.get("c1_confirmed"):
            c1_beta[tid] = row.get("b_fx")
    if set(c1_beta) != set(FROZEN_IDS):
        blockers.append("PUBLISHED_C1_BETA_BINDING_MISSING")
    return {
        "pass": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "records": records,
        "c1_dates": c1_dates,
        "development_dates": dev_dates,
        "eligible_dates": eligible,
        "c1_primary_b_fx": c1_beta,
        "permutation_sha256": shuffle.get("permutation_sha256"),
        "precommit_sha256": a13.get("precommit_sha256"),
        "c1_confirmed_set_sha256": bound.get("c1_confirmed_set_sha256"),
    }
