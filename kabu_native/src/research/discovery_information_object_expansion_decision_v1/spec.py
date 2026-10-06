"""Parent pin, hashes, already-executed check. No outcome mining."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.discovery_information_object_expansion_decision_v1 import (
    ANALYSIS_ID,
    PIN_BASE_QUALIFIED_N,
    PIN_CANARY_PF,
    PIN_CANARY_PNL,
    PIN_CANARY_SIGNAL_N,
    PIN_CANARY_TRADE_N,
    PIN_COVERAGE_PASS_N,
    PIN_G1_G5_SURVIVOR_N,
    PIN_MECHANISM_LIBRARY_N,
    PIN_SELECTABLE_N,
    REQUIRED_PARENT_VERDICT,
)
from research.discovery_information_object_expansion_decision_v1.isolation import RESEARCH_ROOT

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "objects.py",
    "schema_audit.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

PARENT_REPORT = RESEARCH_ROOT / "full_causal_mechanism_discovery_v1" / "report.json"
IOAR_REPORT = RESEARCH_ROOT / "integrated_order_flow_absorption_reversal" / "20260725_192805" / "report.json"
UEIA_REPORT = RESEARCH_ROOT / "upward_edge_identification_audit" / "20260725_202310" / "report.json"


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
    can = dict(prev.get("canary") or {})
    obs = dict(can.get("observed") or {})
    integ = dict(prev.get("integrity") or {})
    freeze = dict(prev.get("candidate_set") or {})
    verdict = str(d.get("VERDICT") or "")
    lib_n = int(freeze.get("FULL_CAUSAL_CANDIDATE_N") or a.get("9_mechanism_library_N") or -1)
    sel_n = int(freeze.get("SELECTABLE_FULL_CAUSAL_CANDIDATE_N") or a.get("21_SELECTABLE_FULL_CAUSAL_CANDIDATE_N") or -1)
    cov_n = int(prev.get("coverage_pass_n") if prev.get("coverage_pass_n") is not None else -1)
    g15 = int(prev.get("g1_g5_survivor_n") if prev.get("g1_g5_survivor_n") is not None else -1)
    bq = int(prev.get("BASE_QUALIFIED_N") if prev.get("BASE_QUALIFIED_N") is not None else d.get("BASE_QUALIFIED_N") or -1)
    pass_n = integ.get("PASS_N")
    total_n = integ.get("TOTAL_N")
    sig = obs.get("signal_n")
    tr = obs.get("trade_n")
    pnl = obs.get("TOTAL_PNL")
    pf = obs.get("PF")
    ok = (
        verdict == REQUIRED_PARENT_VERDICT
        and str(d.get("CASE") or "") == "C"
        and lib_n == int(PIN_MECHANISM_LIBRARY_N)
        and sel_n == int(PIN_SELECTABLE_N)
        and cov_n == int(PIN_COVERAGE_PASS_N)
        and g15 == int(PIN_G1_G5_SURVIVOR_N)
        and bq == int(PIN_BASE_QUALIFIED_N)
        and int(pass_n or -1) == 25
        and int(total_n or -1) == 25
        and int(sig or -1) == int(PIN_CANARY_SIGNAL_N)
        and int(tr or -1) == int(PIN_CANARY_TRADE_N)
        and pnl is not None
        and abs(float(pnl) - float(PIN_CANARY_PNL)) < 1e-6
        and pf is not None
        and abs(float(pf) - float(PIN_CANARY_PF)) < 1e-12
        and bool(can.get("HARD_PASS")) is True
    )
    return {
        "ok": bool(ok),
        "PARENT_ID": "FULL_CAUSAL_MECHANISM_DISCOVERY_V1",
        "REQUIRED_PARENT_VERDICT": REQUIRED_PARENT_VERDICT,
        "observed_verdict": verdict,
        "CASE": d.get("CASE"),
        "MECHANISM_LIBRARY_N": lib_n,
        "SELECTABLE_FULL_CAUSAL_CANDIDATE_N": sel_n,
        "COVERAGE_PASS_N": cov_n,
        "G1_G5_SURVIVOR_N": g15,
        "BASE_QUALIFIED_N": bq,
        "integrity_PASS_N_total": f"{pass_n}/{total_n}",
        "canary_signal_n": sig,
        "canary_trade_n": tr,
        "canary_TOTAL_PNL": pnl,
        "canary_PF": pf,
        "canary_HARD_PASS": can.get("HARD_PASS"),
        "CURRENT_O1_O2_O3_LINE_CLOSED": True,
        "CURRENT_42_RETUNE": False,
        "ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN": False,
        "ENTRY_SIDE_CAUSED_FAILURE": False,
        "EXIT_SIDE_CAUSED_FAILURE": False,
        "FAILURE_BELONGS_TO": "JOINT_FULL_STRATEGY_IDENTITY",
        "ANALYSIS_ID": ANALYSIS_ID,
    }


def pin_ioar() -> dict[str, Any]:
    prev = _load(IOAR_REPORT)
    block = dict(prev.get("verdict") or {})
    final = str(block.get("final_verdict") or prev.get("final_verdict") or "")
    codes = [str(x) for x in (block.get("codes") or [])]
    fail = str(prev.get("fail_cause") or "")
    hyp = str((prev.get("completion") or {}).get("5_hypothesis") or "")
    ok = final == "IOAR_STRATEGY_REJECTED" and (
        fail == "IOAR_HYPOTHESIS_NO_EDGE" or "IOAR_HYPOTHESIS_NO_EDGE" in codes
    )
    return {
        "ok": bool(ok),
        "path": str(IOAR_REPORT),
        "final_verdict": final,
        "fail_cause": fail,
        "hypothesis": hyp,
        "codes_has_hypothesis_no_edge": "IOAR_HYPOTHESIS_NO_EDGE" in codes,
        "EXACT_MECHANISM_CLOSED": True,
        "BROADER_ORDER_FLOW_FAMILY_AUTOMATICALLY_CLOSED": False,
        "ABSORPTION_REVERSAL_EXACT_MECHANISM_SELECTABLE": False,
    }


def pin_ueia() -> dict[str, Any]:
    prev = _load(UEIA_REPORT)
    a = dict(prev.get("answers") or {})
    final = str(prev.get("edge_identification_status") or a.get("59_final_verdict") or a.get("50_edge_status") or "")
    return {
        "ok": final == "UEIA_NO_VALIDATED_EDGE",
        "path": str(UEIA_REPORT),
        "final_verdict": final,
        "UEIA_REOPEN": False,
        "PREOPEN_EDGE_CONTAMINATION_NOTED": True,
    }


def already_executed_check(*, spec_sha: str) -> dict[str, Any]:
    from research.discovery_information_object_expansion_decision_v1.isolation import OUT

    path = OUT / "report.json"
    if not path.is_file():
        return {"REUSED_EXISTING_RESULT": False}
    prev = json.loads(path.read_text(encoding="utf-8"))
    hashes = dict(prev.get("hashes") or {})
    if str(hashes.get("INFORMATION_OBJECT_SPEC_SHA256") or "") == str(spec_sha) and prev.get("decision"):
        return {"REUSED_EXISTING_RESULT": True, "prior_report": prev}
    return {"REUSED_EXISTING_RESULT": False}


assert dumps_sha256
