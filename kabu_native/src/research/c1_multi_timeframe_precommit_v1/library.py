"""Exactly 10 same-family MTF identities. No 11th. No cross-family. No backfill."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_precommit_v1 import (
    HTF_WIDTH_SEC,
    MAX_RAW_CANDIDATE_N,
    POSITION_CAP,
    RAW_CANDIDATE_IDS,
    SHARES,
    STATE_IDS,
)
from research.c1_multi_timeframe_precommit_v1.one_min_states import one_min_registry
from research.c1_multi_timeframe_precommit_v1.spec import (
    exact_entry_rule,
    execution_contract,
    exit_contract,
    portfolio_contract,
)


def _htf_from_id(cid: str) -> str:
    if cid.startswith("MTF_3M__"):
        return "HTF_3M"
    if cid.startswith("MTF_5M__"):
        return "HTF_5M"
    raise RuntimeError(f"BAD_ID {cid}")


def _state_from_id(cid: str) -> str:
    sid = cid.split("__", 1)[1]
    if sid not in STATE_IDS:
        raise RuntimeError(f"BAD_STATE {cid}")
    return sid


def build_raw_library() -> list[dict[str, Any]]:
    by = {r["STATE_ID"]: r for r in one_min_registry()}
    tail = {
        "EXECUTION": execution_contract()["EXEC_ID"],
        "EXIT": exit_contract()["EXIT_ID"],
        "SHARES": int(SHARES),
        "CAP": int(POSITION_CAP),
        "same_symbol": True,
        "occupancy": True,
        "slot_release": True,
        "reentry": True,
        "CROSS_FAMILY": False,
        "HTF_ONSET_REQUIRED": False,
        "HTF_AGE_REQUIRED": False,
        "HTF_PERSISTENCE_REQUIRED": False,
        "STATIC_AND": False,
        "TEMPLATE": "MTF_1M_ONSET_PLUS_COMPLETED_SAME_FAMILY_HTF_CONTEXT",
    }
    raw: list[dict[str, Any]] = []
    for cid in RAW_CANDIDATE_IDS:
        htf = _htf_from_id(cid)
        sid = _state_from_id(cid)
        fam = by[sid]["FAMILY"]
        raw.append(
            {
                "CANDIDATE_ID": cid,
                "FAMILY": fam,
                "STATE_ID": sid,
                "HTF_ID": htf,
                "HTF_WIDTH_SEC": float(HTF_WIDTH_SEC[htf]),
                "ENTRY_STATE_MACHINE": (
                    f"1m {sid} FALSE→TRUE at completed 1m t0; AND latest completed {htf} {sid} "
                    "with finalize_t<=t0 published before 1m eval is TRUE. Same family only."
                ),
                "EXACT_ENTRY_RULE": exact_entry_rule(),
                "EVALUATION_CLOCK": "COMPLETED_1M_BAR_PLUS_HTF_ASOF",
                **tail,
            }
        )
    if len(raw) != int(MAX_RAW_CANDIDATE_N):
        raise RuntimeError("RAW_CANDIDATE_N")
    if [r["CANDIDATE_ID"] for r in raw] != list(RAW_CANDIDATE_IDS):
        raise RuntimeError("RAW_ID_ORDER")
    if any(r["CROSS_FAMILY"] for r in raw):
        raise RuntimeError("CROSS_FAMILY")
    assert portfolio_contract()["CAP"] == int(POSITION_CAP)
    return raw
