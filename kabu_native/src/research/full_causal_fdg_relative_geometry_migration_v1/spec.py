"""Parent pins and frozen migration strategy spec. Frozen before economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.full_causal_fdg_relative_geometry_migration_v1 import (
    ANALYSIS_ID,
    ARCHITECTURE_CLASS,
    CLOSED_STATIC_STRATEGY_ID,
    DEEP_RANKS_REQUIRED,
    EXECUTION_ID,
    PIN_ASK_HOLE_N,
    PIN_BID_HOLE_N,
    PIN_STATIC_DAYS,
    PIN_STATIC_PNL,
    PIN_STATIC_PF,
    PIN_STATIC_TRADE_N,
    PIN_STATIC_UNFILLED,
    POSITION_CAP,
    PREOPEN_EXECUTION_VALID,
    REQUIRED_OBJECT_ID,
    REQUIRED_OBJECT_SHA256,
    REQUIRED_OBJECT_VERDICT,
    REQUIRED_STATIC_VERDICT,
    SESSION_EXIT_FIX_AS_RESCUE,
    SESSION_FLATTEN_HM,
    STATIC_SPAN_FAMILY_CLOSED,
    STATIC_SPAN_RETUNE,
    STATIC_SPAN_STRATEGY_CLOSED,
    STRATEGY_ID,
    TECHNICAL_EXIT_ID,
)
from research.full_causal_fdg_relative_geometry_migration_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "geometry.py",
    "harvest.py",
    "integrity.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

OBJECT_REPORT = RESEARCH_ROOT / "discovery_information_object_expansion_decision_v1" / "report.json"
STATIC_REPORT = RESEARCH_ROOT / "full_causal_strategy_architecture_from_information_object_v1" / "report.json"


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def pin_object_parent() -> dict[str, Any]:
    prev = _load(OBJECT_REPORT)
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    hashes = dict(prev.get("hashes") or {})
    spec = dict(prev.get("spec") or {})
    verdict = str(d.get("VERDICT") or a.get("38_VERDICT") or "")
    obj = str(d.get("SELECTED_OBJECT_ID") or a.get("21_selected_object_ID") or "")
    sha = str(hashes.get("INFORMATION_OBJECT_SPEC_SHA256") or spec.get("INFORMATION_OBJECT_SPEC_SHA256") or a.get("30_INFORMATION_OBJECT_SPEC_SHA256") or "")
    ok = verdict == REQUIRED_OBJECT_VERDICT and obj == REQUIRED_OBJECT_ID and sha == REQUIRED_OBJECT_SHA256
    return {
        "ok": bool(ok),
        "PARENT_ID": "DISCOVERY_INFORMATION_OBJECT_EXPANSION_DECISION_V1",
        "observed_verdict": verdict,
        "SELECTED_OBJECT_ID": obj,
        "INFORMATION_OBJECT_SPEC_SHA256": sha,
    }


def pin_static_parent() -> dict[str, Any]:
    prev = _load(STATIC_REPORT)
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    ev = dict(prev.get("evaluated") or {})
    verdict = str(d.get("VERDICT") or a.get("95_VERDICT") or "")
    sid = str(a.get("12_STRATEGY_ID") or ev.get("STRATEGY_ID") or "")
    trade_n = int(a.get("47_trade_n") if a.get("47_trade_n") is not None else ev.get("trade_n") or -1)
    pnl = a.get("61_TOTAL_PNL")
    pf = a.get("62_PF")
    unf = int(a.get("54_session_exit_unfilled_N") if a.get("54_session_exit_unfilled_N") is not None else -1)
    days = str(a.get("64_pos_neg_zero_days") or "")
    bid_hole = a.get("6_bid_internal_zero_deeper_nonzero_N")
    ask_hole = a.get("7_ask_internal_zero_deeper_nonzero_N")
    holes_ok = bid_hole is not None and ask_hole is not None and int(bid_hole) == PIN_BID_HOLE_N and int(ask_hole) == PIN_ASK_HOLE_N
    rank_ok = bool(a.get("10_DEPTH_RANK_SEMANTICS_VALID"))
    ok = (
        verdict == REQUIRED_STATIC_VERDICT
        and sid == CLOSED_STATIC_STRATEGY_ID
        and trade_n == PIN_STATIC_TRADE_N
        and pnl is not None
        and abs(float(pnl) - float(PIN_STATIC_PNL)) < 1e-6
        and pf is not None
        and abs(float(pf) - float(PIN_STATIC_PF)) < 1e-12
        and unf == PIN_STATIC_UNFILLED
        and days == PIN_STATIC_DAYS
        and holes_ok
        and rank_ok
        and STATIC_SPAN_RETUNE is False
        and SESSION_EXIT_FIX_AS_RESCUE is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "FULL_CAUSAL_STRATEGY_ARCHITECTURE_FROM_INFORMATION_OBJECT_V1",
        "observed_verdict": verdict,
        "CLOSED_STRATEGY_ID": sid,
        "trade_n": trade_n,
        "TOTAL_PNL": pnl,
        "PF": pf,
        "session_exit_unfilled_n": unf,
        "pos_neg_zero": days,
        "DEPTH_RANK_SEMANTICS_VALID": rank_ok,
        "bid_internal_zero_then_nonzero_n": a.get("6_bid_internal_zero_deeper_nonzero_N"),
        "ask_internal_zero_then_nonzero_n": a.get("7_ask_internal_zero_deeper_nonzero_N"),
        "STATIC_SPAN_STRATEGY_CLOSED": True,
        "STATIC_SPAN_RETUNE": False,
        "SESSION_EXIT_FIX_AS_RESCUE": False,
        "STATIC_SPAN_FAMILY_CLOSED": True,
    }


def strategy_freeze_body() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "parent_object_SHA": REQUIRED_OBJECT_SHA256,
        "failed_static_STRATEGY_ID": CLOSED_STATIC_STRATEGY_ID,
        "STRATEGY_ID": STRATEGY_ID,
        "ARCHITECTURE_CLASS": ARCHITECTURE_CLASS,
        "VALID_FULL_DEPTH": (
            "All Buy1..10 and Sell1..10 Price>0 Qty>0, strictly ordered, Buy1<Sell1. Else UNKNOWN."
        ),
        "BID_REL_K": "Buy1.Price - BuyK.Price for k=2..10; finite >0",
        "ASK_REL_K": "SellK.Price - Sell1.Price for k=2..10; finite >0",
        "MIGRATION_PAIR": "immediately consecutive same-symbol captured snapshots, both VALID; no UNKNOWN bridge",
        "BID_GEOMETRY_CONTRACTS": "all k: BID_REL_K[c] <= BID_REL_K[p] and at least one strict <",
        "ASK_GEOMETRY_EXPANDS": "all k: ASK_REL_K[c] >= ASK_REL_K[p] and at least one strict >",
        "FAVORABLE_RELATIVE_GEOMETRY_MIGRATION": "BID_GEOMETRY_CONTRACTS AND ASK_GEOMETRY_EXPANDS",
        "ONSET": "previous known migration-event FALSE AND current TRUE; initial TRUE does not enter; UNKNOWN resets known state",
        "BASELINE": "freeze BID_REL_K[p] and ASK_REL_K[p] at ENTRY pair p→c; immutable",
        "THESIS_VALID": "all k BID_REL_K <= BID_BASE_K AND all k ASK_REL_K >= ASK_BASE_K on latest VALID snapshot",
        "TECHNICAL_EXIT": TECHNICAL_EXIT_ID + ": first later VALID snapshot with THESIS_VALID=false, then first causal Bid1",
        "X1": EXECUTION_ID,
        "CAP": int(POSITION_CAP),
        "same_symbol": True,
        "signal_reserves_slot": False,
        "occupancy": "begins actual ENTRY fill; remains OPEN and EXIT_PENDING; ends actual EXIT fill",
        "slot_release": "actual EXIT fill only",
        "reentry": "release + new FALSE→TRUE migration onset",
        "session_flatten": f"{SESSION_FLATTEN_HM[0]:02d}:{SESSION_FLATTEN_HM[1]:02d}",
        "timestamp": "ingress received_at; ranks 2..10 inherit carrying quote-update; AskTime/BidTime then ingress; never CurrentPriceTime",
        "PREOPEN_EXECUTION_VALID": PREOPEN_EXECUTION_VALID,
        "DEEP_RANKS_REQUIRED": DEEP_RANKS_REQUIRED,
        "STATIC_SPAN_FAMILY_CLOSED": STATIC_SPAN_FAMILY_CLOSED,
        "STATIC_SPAN_RETUNE": STATIC_SPAN_RETUNE,
        "SESSION_EXIT_FIX_AS_RESCUE": SESSION_EXIT_FIX_AS_RESCUE,
        "magnitude_threshold": False,
        "qty_in_alpha": False,
        "k_subset": False,
        "time_window": False,
        "persistence": False,
        "TIMEOUT": False,
        "ALTERNATE_EXIT": False,
    }


def freeze_strategy_spec() -> dict[str, Any]:
    body = strategy_freeze_body()
    sha = dumps_sha256(body)
    return {**body, "FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1": sha, "STRATEGY_FROZEN_BEFORE_ECONOMICS": True}


def already_executed_check(*, spec_sha: str) -> dict[str, Any]:
    from research.full_causal_fdg_relative_geometry_migration_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    hashes = dict(prev.get("hashes") or {})
    if str(hashes.get("FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1") or "") != str(spec_sha):
        return {"REUSED_EXISTING_RESULT": False}
    if dict(prev.get("decision") or {}).get("VERDICT"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


assert dumps_sha256
assert STATIC_SPAN_STRATEGY_CLOSED is True
