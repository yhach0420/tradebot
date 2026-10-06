"""Parent pin, strategy freeze SHA. Frozen before economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.full_causal_strategy_architecture_from_information_object_v1 import (
    ANALYSIS_ID,
    ARCHITECTURE_CLASS,
    CURRENT_42_RETUNE,
    DEPTH_MIGRATION_RULE_USED,
    EXECUTION_ID,
    FORBIDDEN_TRANSFORMS,
    INTERNAL_HOLE_RATE_MAX,
    POSITION_CAP,
    PREOPEN_EXECUTION_VALID,
    RANK_PRESENCE_AS_PRIMARY_ALPHA,
    REQUIRED_OBJECT_ID,
    REQUIRED_OBJECT_SHA256,
    REQUIRED_PARENT_VERDICT,
    SESSION_FLATTEN_HM,
    STRATEGY_ID,
    TECHNICAL_EXIT_ID,
)
from research.full_causal_strategy_architecture_from_information_object_v1.isolation import RESEARCH_ROOT

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

PARENT_REPORT = RESEARCH_ROOT / "discovery_information_object_expansion_decision_v1" / "report.json"


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


def pin_parent() -> dict[str, Any]:
    prev = _load(PARENT_REPORT)
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    spec = dict(prev.get("spec") or {})
    hashes = dict(prev.get("hashes") or {})
    sel = prev.get("selected") if isinstance(prev.get("selected"), dict) else {}
    verdict = str(d.get("VERDICT") or a.get("38_VERDICT") or "")
    obj = str(d.get("SELECTED_OBJECT_ID") or a.get("21_selected_object_ID") or (sel or {}).get("OBJECT_ID") or "")
    sha = str(
        hashes.get("INFORMATION_OBJECT_SPEC_SHA256")
        or spec.get("INFORMATION_OBJECT_SPEC_SHA256")
        or a.get("30_INFORMATION_OBJECT_SPEC_SHA256")
        or ""
    )
    elig = int(d.get("ELIGIBLE_INFORMATION_OBJECT_N") if d.get("ELIGIBLE_INFORMATION_OBJECT_N") is not None else a.get("20_eligible_object_N") or -1)
    day_n = int(a.get("22_selected_object_DEV_day_N") if a.get("22_selected_object_DEV_day_N") is not None else -1)
    outcome = int(prev.get("OUTCOME_READ_N") if prev.get("OUTCOME_READ_N") is not None else a.get("OUTCOME_READ_N") or -1)
    eco = bool(prev.get("ECONOMICS_RUN"))
    case = str(d.get("CASE") or "")
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and case == "A"
        and obj == REQUIRED_OBJECT_ID
        and sha == REQUIRED_OBJECT_SHA256
        and elig == 1
        and day_n == 10
        and outcome == 0
        and eco is False
        and CURRENT_42_RETUNE is False
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "DISCOVERY_INFORMATION_OBJECT_EXPANSION_DECISION_V1",
        "observed_verdict": verdict,
        "CASE": case,
        "SELECTED_OBJECT_ID": obj,
        "INFORMATION_OBJECT_SPEC_SHA256": sha,
        "ELIGIBLE_INFORMATION_OBJECT_N": elig,
        "DEV_DAY_N": day_n,
        "OUTCOME_READ_N": outcome,
        "ECONOMICS_RUN": eco,
        "CURRENT_42_RETUNE": False,
        "ANALYSIS_ID": ANALYSIS_ID,
    }


def strategy_freeze_body() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "parent_object_SHA": REQUIRED_OBJECT_SHA256,
        "SELECTED_OBJECT_ID": REQUIRED_OBJECT_ID,
        "STRATEGY_ID": STRATEGY_ID,
        "ARCHITECTURE_CLASS": ARCHITECTURE_CLASS,
        "VALID_FULL_DEPTH": (
            "All Buy1..Buy10 Price>0 Qty>0 AND all Sell1..Sell10 Price>0 Qty>0 AND "
            "Buy1.Price>Buy2.Price>...>Buy10.Price AND Sell1.Price<Sell2.Price<...<Sell10.Price "
            "AND Buy1.Price<Sell1.Price. Else FULL_DEPTH_STATE=UNKNOWN. UNKNOWN is not FALSE."
        ),
        "BID_SPAN": "Buy1.Price - Buy10.Price; finite and >0; all 10 bid ranks; no qty weight",
        "ASK_SPAN": "Sell10.Price - Sell1.Price; finite and >0; all 10 ask ranks; no qty weight",
        "LONG_GEOMETRY_STATE": "true iff ASK_SPAN > BID_SPAN; false iff ASK_SPAN <= BID_SPAN; no epsilon",
        "UNKNOWN_SEMANTICS": "UNKNOWN does not create ENTRY transition and does not trigger technical EXIT",
        "ENTRY": "previous known FALSE AND current known TRUE (PRICE_GEOMETRY_ASYMMETRY_ONSET)",
        "INITIAL_TRUE_ENTERS": False,
        "PERSISTENCE_WINDOW": False,
        "X1": EXECUTION_ID,
        "TECHNICAL_EXIT": TECHNICAL_EXIT_ID + ": first later VALID_FULL_DEPTH with ASK_SPAN <= BID_SPAN, then first causal Bid1",
        "TIMEOUT": False,
        "ALTERNATE_EXIT": False,
        "CAP": int(POSITION_CAP),
        "same_symbol": True,
        "occupancy": "begins actual ENTRY fill; remains OPEN and EXIT_PENDING; ends actual EXIT fill",
        "signal_reserves_slot": False,
        "slot_release": "actual EXIT fill only",
        "reentry": "previous slot released AND new observed FALSE→TRUE onset; already-TRUE cannot immediately reenter",
        "session_flatten": f"{SESSION_FLATTEN_HM[0]:02d}:{SESSION_FLATTEN_HM[1]:02d} SESSION_EXIT_PENDING then first causal Bid1",
        "timestamp": "ingress received_at; levels 2..10 inherit carrying quote-update; freshness AskTime/BidTime then ingress; never CurrentPriceTime",
        "PREOPEN_EXECUTION_VALID": PREOPEN_EXECUTION_VALID,
        "RANK_PRESENCE_AS_PRIMARY_ALPHA": RANK_PRESENCE_AS_PRIMARY_ALPHA,
        "DEPTH_MIGRATION_RULE_USED": DEPTH_MIGRATION_RULE_USED,
        "FORBIDDEN_TRANSFORMS": list(FORBIDDEN_TRANSFORMS),
        "INTERNAL_HOLE_RATE_MAX": INTERNAL_HOLE_RATE_MAX,
        "CURRENT_42_RETUNE": False,
        "k_subset": False,
        "qty_imbalance_alpha": False,
    }


def freeze_strategy_spec() -> dict[str, Any]:
    body = strategy_freeze_body()
    sha = dumps_sha256(body)
    return {**body, "FULL_STRATEGY_SPEC_SHA256_FDG_V1": sha, "STRATEGY_FROZEN_BEFORE_ECONOMICS": True}


def already_executed_check(*, spec_sha: str) -> dict[str, Any]:
    from research.full_causal_strategy_architecture_from_information_object_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    hashes = dict(prev.get("hashes") or {})
    if str(hashes.get("FULL_STRATEGY_SPEC_SHA256_FDG_V1") or "") != str(spec_sha):
        return {"REUSED_EXISTING_RESULT": False}
    d = dict(prev.get("decision") or {})
    if d.get("VERDICT"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


assert dumps_sha256
