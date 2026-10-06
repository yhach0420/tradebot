"""Offline systematic state-transition library precommit. No harvest/Stress/future/economics."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.systematic_state_transition_library_precommit_v1 import ANALYSIS_ID
from research.systematic_state_transition_library_precommit_v1.analyze import (
    build_answers,
    closed_lineage_rows,
    decide,
)
from research.systematic_state_transition_library_precommit_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.systematic_state_transition_library_precommit_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.systematic_state_transition_library_precommit_v1.spec import canonical_spec, source_sha256, spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "STRESS_RAW_READ_N": 0,
        "STRESS_FILE_OPEN_N": 0,
        "STRESS_NEW_METRIC_N": 0,
        "STRESS_NEW_REPLAY_N": 0,
        "FUTURE_DATA_N": 0,
        "BURNED_HOLDOUT_READ_N": 0,
        "ECONOMICS_RUN": False,
        "SIZING": False,
        "PRIOR_84_GRID_EXECUTED": False,
        "PFQ_RECON_RUN": False,
    }


def _publish(report: dict[str, Any]) -> None:
    d = dict(report.get("decision") or {})
    report["answers"] = build_answers(d)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    gates = {
        **{f"coverage_{k}": v for k, v in (d.get("coverage_gates") or {}).items()},
        **{f"economic_{k}": v for k, v in (d.get("economic_gates") or {}).items()},
        **{f"stability_{k}": v for k, v in (d.get("stability_gates") or {}).items()},
    }
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items() if not str(k).startswith("_")}),
        "inventory_correction": list(d.get("inventory_correction") or []),
        "pfq_closure": kv_rows(d.get("pfq_closure")),
        "or_closure": kv_rows(d.get("or_closure")),
        "state_registry": list(d.get("state_registry") or []),
        "raw_library": list(d.get("raw_library") or []),
        "duplicate_map": list(d.get("duplicate_map") or []),
        "closed_lineage": closed_lineage_rows(list(d.get("duplicate_map") or [])),
        "final_library": list(d.get("final_library") or []),
        "execution": kv_rows(d.get("execution")),
        "exit": kv_rows(d.get("exit")),
        "portfolio": kv_rows(d.get("portfolio")),
        "folds": kv_rows(d.get("folds")),
        "gates": kv_rows(gates),
        "hashes": kv_rows(d.get("hashes")),
        "decision": kv_rows(
            {
                "CASE": d.get("CASE"),
                "CASE_NAME": d.get("CASE_NAME"),
                "VERDICT": d.get("VERDICT"),
                "NEXT": d.get("NEXT"),
                "EXISTING_ARCHITECTURE_EXHAUSTED": d.get("EXISTING_ARCHITECTURE_EXHAUSTED"),
                "AVAILABLE_STATE_N": d.get("AVAILABLE_STATE_N"),
                "RAW_CANDIDATE_N": (d.get("counts") or {}).get("RAW_CANDIDATE_N"),
                "FINAL_CANDIDATE_N": (d.get("counts") or {}).get("FINAL_CANDIDATE_N"),
                "ECONOMICS_RUN": False,
                "PRIOR_84_GRID_EXECUTED": False,
            }
        ),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> None:
    set_research_priority_below_normal()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or ""),
        str((before.get("paper") or {}).get("session_dir") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    pack = decide()
    after = snapshot(phase="POST")
    touched = []
    for snap in (before, after):
        cap = (snap.get("capture") or {}).get("active_dir") or ""
        if cap:
            touched.append(Path(cap))
    if stress_path_touch_n(touched) or holdout_path_touch_n(touched):
        raise RuntimeError("SEALED_PATH_TOUCH")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": source_sha256(),
        "decision": pack,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _publish(report)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"CASE {pack['CASE_NAME']}", flush=True)
    print(f"VERDICT {pack['VERDICT']}", flush=True)
    print(f"NEXT {pack['NEXT']}", flush=True)
    print(f"AVAILABLE_STATE_N {pack['AVAILABLE_STATE_N']}", flush=True)
    print(f"RAW {pack['counts']['RAW_CANDIDATE_N']}", flush=True)
    print(f"FINAL {pack['counts']['FINAL_CANDIDATE_N']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
