"""Bind the four final parent mechanisms. No symbol outcomes."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_transmission_precommit import (
    PARENT_ANALYSIS_ID,
    PARENT_NEXT,
    PARENT_PRECOMMIT_ID,
    PARENT_PRECOMMIT_SHA256,
    PARENT_VERDICT,
)
from research.causal_driver_pb1.sector_state_transmission_precommit.isolation import DISC_V2_OUT, PARENT_OUT, V13_OUT

MECHANISMS = (
    {
        "parent_mechanism_id": "M1",
        "test_id": "BREADTH|GLOBAL_105|w1|h1",
        "metric": "BREADTH",
        "scope_id": "GLOBAL_105",
        "sector_id": None,
        "lookback": 1,
        "horizon": 1,
        "direction": "UP",
        "global": True,
    },
    {
        "parent_mechanism_id": "M2",
        "test_id": "BREADTH|GLOBAL_105|w1|h3",
        "metric": "BREADTH",
        "scope_id": "GLOBAL_105",
        "sector_id": None,
        "lookback": 1,
        "horizon": 3,
        "direction": "UP",
        "global": True,
    },
    {
        "parent_mechanism_id": "M3",
        "test_id": "BREADTH|SECTOR_3650|w1|h3",
        "metric": "BREADTH",
        "scope_id": "SECTOR_3650",
        "sector_id": "3650",
        "lookback": 1,
        "horizon": 3,
        "direction": "UP",
        "global": False,
    },
    {
        "parent_mechanism_id": "M4",
        "test_id": "BREADTH|SECTOR_3650|w3|h1",
        "metric": "BREADTH",
        "scope_id": "SECTOR_3650",
        "sector_id": "3650",
        "lookback": 3,
        "horizon": 1,
        "direction": "UP",
        "global": False,
    },
)


def parent_mechanism_set_sha256() -> str:
    payload = {
        "namespace": "SECTOR_STATE_SYMBOL_TRANSMISSION_PARENT_MECHANISMS_V1",
        "parent_verdict": PARENT_VERDICT,
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "mechanisms": [dict(m) for m in MECHANISMS],
    }
    return sha256_obj(payload)


def bind_parent() -> dict[str, Any]:
    blockers: list[str] = []
    parent = json.loads((PARENT_OUT / "report.json").read_text(encoding="utf-8")) if (PARENT_OUT / "report.json").is_file() else {}
    answers = parent.get("answers") or {}
    if answers.get("VERDICT") != PARENT_VERDICT:
        blockers.append("PARENT_VERDICT_MISMATCH")
    if answers.get("NEXT") != PARENT_NEXT:
        blockers.append("PARENT_NEXT_MISMATCH")
    if parent.get("analysis_id") != PARENT_ANALYSIS_ID:
        blockers.append("PARENT_ANALYSIS_MISMATCH")
    if answers.get("precommit_sha256") != PARENT_PRECOMMIT_SHA256:
        blockers.append("PARENT_PRECOMMIT_SHA_MISMATCH")
    finals = [str(r.get("test_id")) for r in (answers.get("final_candidates") or [])]
    expect = [m["test_id"] for m in MECHANISMS]
    if sorted(finals) != sorted(expect) or len(finals) != 4:
        blockers.append("PARENT_FINAL_SET_MISMATCH")
    v13 = json.loads((V13_OUT / "report.json").read_text(encoding="utf-8")) if (V13_OUT / "report.json").is_file() else {}
    if (v13.get("answers") or {}).get("precommit_sha256") != PARENT_PRECOMMIT_SHA256:
        blockers.append("V13_PRECOMMIT_SHA_MISMATCH")
    if (v13.get("answers") or {}).get("precommit_id") != PARENT_PRECOMMIT_ID:
        blockers.append("V13_PRECOMMIT_ID_MISMATCH")
    v2 = json.loads((DISC_V2_OUT / "report.json").read_text(encoding="utf-8")) if (DISC_V2_OUT / "report.json").is_file() else {}
    bound = (v2.get("evaluation") or {}).get("bound") or {}
    dev = list(bound.get("development_dates") or [])
    c1 = list(bound.get("c1_dates") or [])
    eligible = list(bound.get("eligible_dates") or [])
    if not dev or not c1 or not eligible:
        blockers.append("PARENT_ELIGIBLE_DATES_MISSING")
    digest = parent_mechanism_set_sha256()
    return {
        "pass": not blockers,
        "blockers": blockers,
        "mechanisms": [dict(m) for m in MECHANISMS],
        "parent_mechanism_set_sha256": digest,
        "development_dates": dev,
        "c1_dates": c1,
        "eligible_dates": eligible,
        "eligible_day_sha256": (v2.get("answers") or {}).get("eligible_day_sha256"),
        "fold_boundary_sha256": (v2.get("answers") or {}).get("fold_boundary_sha256"),
        "final_test_ids": finals,
    }
