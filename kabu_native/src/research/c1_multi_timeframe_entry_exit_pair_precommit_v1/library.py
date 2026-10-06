"""Exactly 10 frozen MTF ENTRY × X1_IMMEDIATE_ASK × 5 existing EXIT. No 51st. No shorthand X1."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import (
    EXECUTION_ID,
    EXIT_IDS,
    MAX_RAW_STRATEGY_N,
    POSITION_CAP,
    RAW_CANDIDATE_IDS,
    SHARES,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import pair_id
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.c1_multi_timeframe_precommit_v1.spec import execution_contract, portfolio_contract


def build_raw_strategy_library(entries: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    base = list(entries) if entries is not None else build_raw_library()
    if [r["CANDIDATE_ID"] for r in base] != list(RAW_CANDIDATE_IDS):
        raise RuntimeError("ENTRY_LIBRARY_DRIFT")
    if any(r.get("CROSS_FAMILY") for r in base):
        raise RuntimeError("CROSS_FAMILY")
    exec_id = str(execution_contract()["EXEC_ID"])
    if exec_id != EXECUTION_ID:
        raise RuntimeError("EXECUTION_ID_MISMATCH")
    port = portfolio_contract()
    raw: list[dict[str, Any]] = []
    for entry in base:
        for exit_id in EXIT_IDS:
            cid = pair_id(str(entry["CANDIDATE_ID"]), exit_id)
            if EXECUTION_ID not in cid:
                raise RuntimeError("BARE_X1")
            if "__X1__" in cid:
                raise RuntimeError("BARE_X1_TOKEN")
            raw.append(
                {
                    "CANDIDATE_ID": cid,
                    "ENTRY_ID": entry["CANDIDATE_ID"],
                    "FAMILY": entry["FAMILY"],
                    "STATE_ID": entry["STATE_ID"],
                    "HTF_ID": entry["HTF_ID"],
                    "HTF_WIDTH_SEC": entry["HTF_WIDTH_SEC"],
                    "ENTRY_STATE_MACHINE": entry["ENTRY_STATE_MACHINE"],
                    "EXACT_ENTRY_RULE": entry["EXACT_ENTRY_RULE"],
                    "EVALUATION_CLOCK": entry["EVALUATION_CLOCK"],
                    "HTF_CAUSAL_ASOF": (
                        "latest completed same-family HTF with finalize_t<=t0 published "
                        "before 1m eval is TRUE"
                    ),
                    "EXECUTION": EXECUTION_ID,
                    "EXIT": exit_id,
                    "SHARES": int(SHARES),
                    "CAP": int(POSITION_CAP),
                    "same_symbol": True,
                    "occupancy": True,
                    "slot_release": True,
                    "reentry": True,
                    "CROSS_FAMILY": False,
                    "VWAP_GLOBAL_GATE": False,
                    "TEMPLATE": "MTF_1M_ONSET_PLUS_COMPLETED_SAME_FAMILY_HTF_CONTEXT__X1_IMMEDIATE_ASK__EXISTING_SIMPLE_FULL_EXIT",
                    "HTF_ONSET_REQUIRED": False,
                    "HTF_AGE_REQUIRED": False,
                    "HTF_PERSISTENCE_REQUIRED": False,
                    "STATIC_AND": False,
                }
            )
    if len(raw) != int(MAX_RAW_STRATEGY_N):
        raise RuntimeError("RAW_STRATEGY_N")
    ids = [r["CANDIDATE_ID"] for r in raw]
    if len(set(ids)) != len(ids):
        raise RuntimeError("INTERNAL_ID_COLLISION")
    if any(r["EXECUTION"] != EXECUTION_ID for r in raw):
        raise RuntimeError("EXECUTION_DRIFT")
    if any(r["EXIT"] not in EXIT_IDS for r in raw):
        raise RuntimeError("EXIT_DRIFT")
    if int(port["CAP"]) != int(POSITION_CAP):
        raise RuntimeError("CAP_DRIFT")
    return raw
