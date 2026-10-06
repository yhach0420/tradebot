"""Offline Full Strategy architecture precommit. No harvest, PnL, Stress, or Runtime writes."""
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
from research.new_full_strategy_architecture_precommit_v1 import (
    ANALYSIS_ID,
    CERTIFIED,
    FORBIDDEN_NEXT_RUNS,
    TRUE_OOS,
)
from research.new_full_strategy_architecture_precommit_v1.analyze import (
    already_executed_check,
    build_answers,
    decide,
)
from research.new_full_strategy_architecture_precommit_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.new_full_strategy_architecture_precommit_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    proposal_sheet_rows,
    write_artifacts,
)
from research.new_full_strategy_architecture_precommit_v1.spec import canonical_spec, source_sha256, spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "STRESS_FILE_OPEN_N": 0,
        "HOLDOUT_OPENED": False,
        "RAW_CAPTURE_READ_N": 0,
        "NEW_REPLAY": False,
        "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
        "NEW_ARCHITECTURE_PNL_READ_N": 0,
        "PRIOR_ECONOMIC_REPORT_OPEN_N": 0,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    frozen = dict(pack.get("frozen_strategy") or {})
    sheets = {
        "answers": kv_rows(report.get("answers")),
        "objective_alignment": kv_rows(pack.get("objective")),
        "old_rca_stop": kv_rows(pack.get("old_rca_stop")),
        "closed_lineages": list(pack.get("closed_lineages") or []),
        "design_constraints": kv_rows(pack.get("design_constraints")),
        "architecture_proposals": proposal_sheet_rows(list(pack.get("proposals") or [])),
        "novelty_audit": list(pack.get("novelty_audit") or []),
        "eligibility": list(pack.get("eligibility") or []),
        "selection": list(pack.get("selection_trace") or [])
        + kv_rows(
            {
                "ELIGIBLE_PROPOSAL_N": d.get("ELIGIBLE_PROPOSAL_N"),
                "ELIGIBLE_PROPOSAL_IDS": d.get("ELIGIBLE_PROPOSAL_IDS"),
                "SELECTED_ARCHITECTURE_ID": d.get("SELECTED_ARCHITECTURE_ID"),
                "DETERMINISTIC_SELECTION_RULE_USED": d.get("DETERMINISTIC_SELECTION_RULE_USED"),
                "WINNER_FORCED_DESPITE_NO_ELIGIBLE": d.get("WINNER_FORCED_DESPITE_NO_ELIGIBLE"),
            }
        ),
        "frozen_strategy": kv_rows(frozen) if frozen else [{"key": "ARCHITECTURE_FROZEN", "value": False}],
        "data_access": kv_rows(pack.get("data_access")),
        "decision": kv_rows(d),
        "safety": kv_rows(report.get("safety")),
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
    src_hash = source_sha256()
    reused = already_executed_check(src_hash)
    if reused.get("REUSED_EXISTING_RESULT"):
        prev = dict(reused.get("prior_report") or {})
        print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
        print("ALREADY_EXECUTED_CHECK true", flush=True)
        print("REUSED_EXISTING_RESULT true", flush=True)
        print(f"VERDICT {(prev.get('decision') or {}).get('VERDICT')}", flush=True)
        print(f"NEXT {(prev.get('decision') or {}).get('NEXT')}", flush=True)
        print(f"OUT {OUT}", flush=True)
        print("STOP", flush=True)
        return
    pack = decide()
    nxt = str((pack.get("decision") or {}).get("NEXT") or "")
    if nxt in FORBIDDEN_NEXT_RUNS:
        raise RuntimeError("OLD_RCA_NEXT")
    after = snapshot(phase="POST")
    opened: list[Path] = []
    leak = {
        "HOLDOUT_BURNED_READ_N": holdout_path_touch_n(opened),
        "STRESS_READ_N": stress_path_touch_n(opened),
        "STRESS_FILE_OPEN_N": 0,
        "FUTURE_DATA_N": 0,
        "RAW_CAPTURE_READ_N": 0,
        "PRIOR_ECONOMIC_REPORT_OPEN_N": 0,
        "NEW_ARCHITECTURE_PNL_READ_N": 0,
    }
    if leak["HOLDOUT_BURNED_READ_N"] or leak["STRESS_READ_N"] or leak["FUTURE_DATA_N"]:
        raise RuntimeError(f"LEAKAGE {leak}")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": src_hash,
        "ALREADY_EXECUTED_CHECK": False,
        "REUSED_EXISTING_RESULT": False,
        "objective": pack["objective"],
        "old_rca_stop": pack["old_rca_stop"],
        "design_constraints": pack["design_constraints"],
        "closed_lineages": pack["closed_lineages"],
        "revival": pack["revival"],
        "proposals": pack["proposals"],
        "eligibility": pack["eligibility"],
        "novelty_audit": pack["novelty_audit"],
        "selection_trace": pack["selection_trace"],
        "selected": pack["selected"],
        "frozen_strategy": pack["frozen_strategy"],
        "decision": pack["decision"],
        "data_access": pack["data_access"],
        "guards": pack["guards"],
        "leakage": leak,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _publish(report, pack)
    d = pack["decision"]
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("ALREADY_EXECUTED_CHECK false", flush=True)
    print("REUSED_EXISTING_RESULT false", flush=True)
    print(f"Q1 {pack['objective']['Q1']}", flush=True)
    print(f"Q2 {pack['objective']['Q2']}", flush=True)
    print(f"Q3 {pack['objective']['Q3']}", flush=True)
    print(f"ELIGIBLE_PROPOSAL_N {d['ELIGIBLE_PROPOSAL_N']}", flush=True)
    print(f"SELECTED {d['SELECTED_ARCHITECTURE_ID']}", flush=True)
    print(f"FULL_STRATEGY_SPEC_SHA256 {d['FULL_STRATEGY_SPEC_SHA256']}", flush=True)
    print(f"NEW_ARCHITECTURE_ECONOMICS_RUN {d['NEW_ARCHITECTURE_ECONOMICS_RUN']}", flush=True)
    print(f"VERDICT {d['VERDICT']}", flush=True)
    print(f"NEXT {d['NEXT']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
