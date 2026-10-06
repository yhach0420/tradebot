"""Offline inventory-and-freeze V3. No candidate economics. Runtime/Capture writes 0."""
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
from research.new_full_strategy_architecture_inventory_and_freeze_v3 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.new_full_strategy_architecture_inventory_and_freeze_v3.analyze import (
    already_executed_check,
    build_answers,
    decide,
)
from research.new_full_strategy_architecture_inventory_and_freeze_v3.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.new_full_strategy_architecture_inventory_and_freeze_v3.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.new_full_strategy_architecture_inventory_and_freeze_v3.spec import source_sha256

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
        "HOLDOUT_OPENED": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "V4_RCA_RUN": False,
        "V4_RETUNE": False,
        "CSB_RCA_RUN": False,
        "OLD_ST_RCA_CONTINUED": False,
        "ANOTHER_PRECOMMIT_RUN": False,
        "NEW_CANDIDATE_ECONOMICS_RUN": False,
        "NEW_CANDIDATE_PNL_READ_N": 0,
    }


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    inv = list(pack.get("closed_architecture_inventory") or [])
    info = dict(pack.get("available_information") or {})
    novelty = dict(pack.get("novelty_audit") or {})
    props = list(pack.get("architecture_proposals") or [])
    sheets = {
        "answers": kv_rows(report.get("answers")),
        "objective_alignment": kv_rows(pack.get("objective_alignment")),
        "v4_closure": kv_rows(pack.get("v4_closure")),
        "withdrawn_v5": kv_rows(pack.get("withdrawn_v5")),
        "closed_architecture_inventory": inv or [{"empty": True}],
        "pullback_family": kv_rows(pack.get("pullback_family")),
        "available_information": list(info.get("FAMILIES") or [{"empty": True}]),
        "architecture_proposals": props or [{"PROPOSAL_N": 0, "REASON": "NO_FORCED_NEWNESS"}],
        "novelty_audit": list(novelty.get("CONSIDERED_NOT_PROPOSED") or [{"empty": True}]),
        "eligibility": kv_rows(pack.get("eligibility")),
        "selection": kv_rows(pack.get("selection")),
        "frozen_strategy": kv_rows({"ARCHITECTURE_FROZEN": False, "FULL_STRATEGY_SPEC_SHA256_V5": None}),
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
        print(f"VERDICT {(prev.get('decision') or {}).get('VERDICT')}", flush=True)
        print(f"NEXT {(prev.get('decision') or {}).get('NEXT')}", flush=True)
        print(f"OUT {OUT}", flush=True)
        print("STOP", flush=True)
        return
    pack = decide()
    after = snapshot(phase="POST")
    opened: list[Path] = []
    leak = {
        "HOLDOUT_BURNED_READ_N": holdout_path_touch_n(opened),
        "STRESS_READ_N": stress_path_touch_n(opened),
        "FUTURE_DATA_N": 0,
    }
    if leak["HOLDOUT_BURNED_READ_N"] or leak["STRESS_READ_N"] or leak["FUTURE_DATA_N"]:
        raise RuntimeError(f"LEAKAGE {leak}")
    d = dict(pack.get("decision") or {})
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "source_sha256": src_hash,
        "spec_sha256_v5": None,
        "ALREADY_EXECUTED_CHECK": False,
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": pack.get("objective_alignment"),
        "v4_closure": pack.get("v4_closure"),
        "withdrawn_v5": pack.get("withdrawn_v5"),
        "closed_architecture_inventory": pack.get("closed_architecture_inventory"),
        "pullback_family": pack.get("pullback_family"),
        "available_information": pack.get("available_information"),
        "architecture_proposals": pack.get("architecture_proposals"),
        "novelty_audit": pack.get("novelty_audit"),
        "eligibility": pack.get("eligibility"),
        "selection": pack.get("selection"),
        "frozen_strategy": pack.get("frozen_strategy"),
        "decision": d,
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
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("ALREADY_EXECUTED_CHECK false", flush=True)
    print(f"V4_CLOSED {d.get('V4_CLOSED')}", flush=True)
    print(f"PREVIOUS_V5_PROPOSAL_WITHDRAWN {d.get('PREVIOUS_V5_PROPOSAL_WITHDRAWN')}", flush=True)
    print(f"PROPOSAL_N {d.get('PROPOSAL_N')}", flush=True)
    print(f"ELIGIBLE_PROPOSAL_N {d.get('ELIGIBLE_PROPOSAL_N')}", flush=True)
    print(f"FULL_STRATEGY_SPEC_SHA256_V5 {d.get('FULL_STRATEGY_SPEC_SHA256_V5')}", flush=True)
    print(f"VERDICT {d.get('VERDICT')}", flush=True)
    print(f"NEXT {d.get('NEXT')}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
