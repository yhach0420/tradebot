"""Offline C1 ENTRY×EXIT pair library precommit. No harvest/Stress/future/economics."""
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
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import ANALYSIS_ID
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.analyze import build_answers, decide
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.isolation import (
    EXIT_SOURCE_FILE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.prior import (
    artifacts_modified,
    snapshot_prior_artifacts,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import canonical_spec, file_sha256, source_sha256, spec_sha256

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
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "SIZING": False,
        "TECHNICAL_EXIT_NEW_SEARCH": False,
        "EXIT6_CREATED": False,
        "C4_EXECUTED_THIS_RUN": False,
    }


def _publish(report: dict[str, Any]) -> None:
    d = dict(report.get("decision") or {})
    report["answers"] = build_answers(d)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    gates = {
        **{f"coverage_{k}": v for k, v in (d.get("coverage_gates") or {}).items()},
        **{f"fold_{k}": v for k, v in (d.get("fold_coverage_gates") or {}).items()},
        **{f"economic_{k}": v for k, v in (d.get("economic_gates") or {}).items()},
        **{f"stability_{k}": v for k, v in (d.get("stability_gates") or {}).items()},
    }
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items() if not str(k).startswith("_")}),
        "prior_c1": kv_rows(d.get("prior")),
        "entry_library": list(d.get("entry_library") or []),
        "exit_library": list(d.get("exit_library") or []),
        "raw_pairs": list(d.get("raw_library") or []),
        "duplicate_map": list(d.get("duplicate_map") or []),
        "final_pairs": list(d.get("final_library") or []),
        "execution": kv_rows(d.get("execution")),
        "portfolio": kv_rows(d.get("portfolio")),
        "folds": kv_rows(d.get("folds")),
        "gates": kv_rows(gates),
        "canary": kv_rows(d.get("canary")),
        "methodology": kv_rows(d.get("methodology")),
        "hashes": kv_rows(d.get("hashes")),
        "decision": kv_rows(
            {
                "CASE": d.get("CASE"),
                "CASE_NAME": d.get("CASE_NAME"),
                "VERDICT": d.get("VERDICT"),
                "NEXT": d.get("NEXT"),
                "FINAL_STRATEGY_N": (d.get("counts") or {}).get("FINAL_STRATEGY_N"),
                "RAW_STRATEGY_N": (d.get("counts") or {}).get("RAW_STRATEGY_N"),
                "DUPLICATE_N": (d.get("counts") or {}).get("DUPLICATE_N"),
                "CANDIDATE_ECONOMICS_RUN": False,
                "PRIOR_Z3_ONLY_NEXT_SUPERSEDED": True,
                "Z3_ONLY_C1_CLOSURE_FORBIDDEN": True,
                "C4_REMAINS_ELIGIBLE": True,
            }
        ),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> None:
    set_research_priority_below_normal()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str(before.get("ACTIVE_CAPTURE_PATH") or ""),
        str(before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    prior_before = snapshot_prior_artifacts()
    exits_before = file_sha256(EXIT_SOURCE_FILE) if EXIT_SOURCE_FILE.is_file() else ""
    pack = decide()
    after = snapshot(phase="POST")
    prior_after = snapshot_prior_artifacts()
    exits_after = file_sha256(EXIT_SOURCE_FILE) if EXIT_SOURCE_FILE.is_file() else ""
    if artifacts_modified(prior_before, prior_after):
        raise RuntimeError("PRIOR_ARTIFACT_MODIFIED")
    if exits_before != exits_after:
        raise RuntimeError("EXIT_IMPLEMENTATION_CHANGED")
    opened: list[Path] = []
    if stress_path_touch_n(opened) or holdout_path_touch_n(opened):
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
    print(f"RAW {pack['counts']['RAW_STRATEGY_N']}", flush=True)
    print(f"DUP {pack['counts']['DUPLICATE_N']}", flush=True)
    print(f"FINAL {pack['counts']['FINAL_STRATEGY_N']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
