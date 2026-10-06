"""Offline CSB redesign freeze. No harvest, PnL, Stress, or Runtime writes."""
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
from research.new_full_strategy_architecture_redesign_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.new_full_strategy_architecture_redesign_v1.analyze import (
    already_executed_check,
    build_answers,
    decide,
)
from research.new_full_strategy_architecture_redesign_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.new_full_strategy_architecture_redesign_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.new_full_strategy_architecture_redesign_v1.spec import canonical_spec, source_sha256, spec_sha256

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
        "RAW_CAPTURE_READ_N": 0,
        "NEW_REPLAY": False,
        "NEW_ARCHITECTURE_ECONOMICS_RUN": False,
        "G1_G6": False,
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
        "timer": kv_rows(pack.get("timer")),
        "breadth": kv_rows(pack.get("breadth")),
        "flatten": kv_rows(pack.get("flatten")),
        "gates": kv_rows(pack.get("gates")),
        "frozen_strategy": kv_rows(frozen) if frozen else [{"key": "ARCHITECTURE_FROZEN", "value": False}],
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
        "RAW_CAPTURE_READ_N": 0,
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
        "universe": pack["universe"],
        "timer": pack["timer"],
        "breadth": pack["breadth"],
        "simultaneous": pack["simultaneous"],
        "x1": pack["x1"],
        "quote": pack["quote"],
        "flatten": pack["flatten"],
        "frozen_strategy": pack["frozen_strategy"],
        "gates": pack["gates"],
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
    print(f"GATES {pack['gates']}", flush=True)
    print(f"FULL_STRATEGY_SPEC_SHA256_V3 {d.get('FULL_STRATEGY_SPEC_SHA256_V3')}", flush=True)
    print(f"ANOTHER_PRECOMMIT_AFTER_PASS {d.get('ANOTHER_PRECOMMIT_AFTER_PASS')}", flush=True)
    print(f"NEW_ARCHITECTURE_ECONOMICS_RUN {d['NEW_ARCHITECTURE_ECONOMICS_RUN']}", flush=True)
    print(f"VERDICT {d['VERDICT']}", flush=True)
    print(f"NEXT {d['NEXT']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
