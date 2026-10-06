"""Pin FDG + information-object parents. Freeze method-audit spec. No economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.existing_data_strategy_research_stop_reassessment_v1 import (
    ANALYSIS_ID,
    REQUIRED_FDG_DAYS,
    REQUIRED_FDG_EX_BEST,
    REQUIRED_FDG_INTEGRITY,
    REQUIRED_FDG_PF,
    REQUIRED_FDG_PNL,
    REQUIRED_FDG_SIGNAL_N,
    REQUIRED_FDG_SPEC_SHA256,
    REQUIRED_FDG_STRATEGY_ID,
    REQUIRED_FDG_TRADE_N,
    REQUIRED_FDG_UNFILLED,
    REQUIRED_FDG_VERDICT,
    REQUIRED_OBJECT_VERDICT,
    REQUIRED_RAW_OBJECT_N,
    REQUIRED_SELECTED_OBJECT,
    REQUIRED_UNDEREXPLORED_N,
)
from research.existing_data_strategy_research_stop_reassessment_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "inventory.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

FDG_REPORT = RESEARCH_ROOT / "full_causal_fdg_relative_geometry_migration_v1" / "report.json"
OBJECT_REPORT = RESEARCH_ROOT / "discovery_information_object_expansion_decision_v1" / "report.json"


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


def pin_fdg() -> dict[str, Any]:
    prev = _load(FDG_REPORT)
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    hashes = dict(prev.get("hashes") or {})
    ev = dict(prev.get("evaluated") or {})
    verdict = str(d.get("VERDICT") or a.get("100_VERDICT") or "")
    sid = str((prev.get("spec") or {}).get("STRATEGY_ID") or ev.get("STRATEGY_ID") or "")
    spec_sha = str(hashes.get("FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1") or a.get("27_FULL_STRATEGY_SPEC_SHA256_FDG_MIGRATION_V1") or "")
    integ = str(a.get("41_integrity_PASS_N_total") or "")
    canary = bool(a.get("47_canary_parity_PASS"))
    signal_n = int(a.get("48_signal_n") if a.get("48_signal_n") is not None else -1)
    trade_n = int(a.get("50_trade_n") if a.get("50_trade_n") is not None else -1)
    pnl = a.get("64_TOTAL_PNL")
    pf = a.get("65_PF")
    days = str(a.get("67_pos_neg_zero_days") or "")
    exb = a.get("68_EX_BEST_DAY_PNL")
    unf = int(a.get("57_session_exit_unfilled_N") if a.get("57_session_exit_unfilled_N") is not None else -1)
    exhausted = bool(a.get("81_FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED") or d.get("FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED"))
    ok = (
        verdict == REQUIRED_FDG_VERDICT
        and sid == REQUIRED_FDG_STRATEGY_ID
        and spec_sha == REQUIRED_FDG_SPEC_SHA256
        and integ == REQUIRED_FDG_INTEGRITY
        and canary is True
        and signal_n == REQUIRED_FDG_SIGNAL_N
        and trade_n == REQUIRED_FDG_TRADE_N
        and pnl is not None
        and abs(float(pnl) - float(REQUIRED_FDG_PNL)) < 1e-6
        and pf is not None
        and abs(float(pf) - float(REQUIRED_FDG_PF)) < 1e-12
        and days == REQUIRED_FDG_DAYS
        and exb is not None
        and abs(float(exb) - float(REQUIRED_FDG_EX_BEST)) < 1e-6
        and unf == REQUIRED_FDG_UNFILLED
        and exhausted is True
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "FULL_CAUSAL_FDG_RELATIVE_GEOMETRY_MIGRATION_V1",
        "VERDICT": verdict,
        "STRATEGY_ID": sid,
        "SPEC_SHA256": spec_sha,
        "integrity": integ,
        "canary_PASS": canary,
        "signal_n": signal_n,
        "trade_n": trade_n,
        "TOTAL_PNL": pnl,
        "PF": pf,
        "pos_neg_zero": days,
        "EX_BEST_DAY_PNL": exb,
        "session_exit_unfilled_n": unf,
        "FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED": exhausted,
        "FDG_RESCUED": False,
    }


def pin_object() -> dict[str, Any]:
    prev = _load(OBJECT_REPORT)
    d = dict(prev.get("decision") or {})
    a = dict(prev.get("answers") or {})
    verdict = str(d.get("VERDICT") or a.get("38_VERDICT") or "")
    raw_n = int(a.get("9_raw_object_N") if a.get("9_raw_object_N") is not None else -1)
    under_n = int(a.get("14_underexplored_causal_object_N") if a.get("14_underexplored_causal_object_N") is not None else -1)
    selected = str(a.get("21_selected_object_ID") or d.get("SELECTED_OBJECT_ID") or "")
    ok = (
        verdict == REQUIRED_OBJECT_VERDICT
        and raw_n == REQUIRED_RAW_OBJECT_N
        and under_n == REQUIRED_UNDEREXPLORED_N
        and selected == REQUIRED_SELECTED_OBJECT
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "DISCOVERY_INFORMATION_OBJECT_EXPANSION_DECISION_V1",
        "VERDICT": verdict,
        "RAW_OBJECT_N": raw_n,
        "UNDEREXPLORED_CAUSAL_OBJECT_N": under_n,
        "selected": selected,
        "NEW_INFORMATION_OBJECT_SEARCH_CLOSED": True,
    }


def freeze_method_audit(*, eligible_n: int, selected_id: str | None, why: str, r_fail: list[str]) -> dict[str, Any]:
    body = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ELIGIBLE_RESEARCH_METHOD_N": int(eligible_n),
        "SELECTED_METHOD_ID": selected_id,
        "BOUNDED_DATA_DRIVEN_CLASS": "BOUNDED_DATA_DRIVEN_FULL_CAUSAL_STRATEGY_LEARNING",
        "WHY_NOT_PRIOR_EQUIVALENT": (
            "H5 data-first discovery (PROFITABLE_MOVE / representation expansion) used fixed-horizon "
            "raw-signal labels, not Complete Full Causal occupancy. FCMD/SIMPLE_FULL/ST/C1/C4 evaluated "
            "prewritten libraries under Full Causal replay. Those are not automatically the same as "
            "data-learned strategy construction."
        ),
        "WHY_NOT_ELIGIBLE_NOW": why,
        "FAILED_ELIGIBILITY_GATES": list(r_fail),
        "FULL_CAUSAL_REQUIREMENTS": (
            "ENTRY + execution + technical EXIT + CAP + same-symbol + occupancy + slot release + "
            "reentry + session flatten before candidate selection"
        ),
        "CAPACITY_BOUNDARY": "Any remaining generator from existing sealed information collapses to a finite prewritten library already Full-Causal-tested, or else requires unbounded search/ML score.",
        "LEAKAGE_BOUNDARY": "TRAIN-only construction; internal DEV blocks after selection; no Holdout/Stress/future for construction.",
        "NEW_STRATEGY_CREATED": False,
        "NEW_ENTRY_CREATED": False,
        "NEW_EXIT_CREATED": False,
        "NEW_ECONOMIC_RUN": False,
        "THRESHOLD_SEARCH": False,
    }
    sha = dumps_sha256(body)
    return {**body, "REMAINING_RESEARCH_METHOD_SPEC_SHA256": sha}


def already_executed_check() -> dict[str, Any]:
    from research.existing_data_strategy_research_stop_reassessment_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"REUSED_EXISTING_RESULT": False}
    if dict(prev.get("decision") or {}).get("VERDICT"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


assert dumps_sha256
