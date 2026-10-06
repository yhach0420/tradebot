"""Offline existing-architecture exhaustion review. Inventory only. No harvest/Stress/future."""
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
from research.existing_architecture_exhaustion_review_v1 import ANALYSIS_ID
from research.existing_architecture_exhaustion_review_v1.analyze import build_answers, decide, remaining_eligible
from research.existing_architecture_exhaustion_review_v1.isolation import (
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.existing_architecture_exhaustion_review_v1.inventory import ROW_FIELDS
from research.existing_architecture_exhaustion_review_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    closure_rows,
    duplicate_rows,
    kv_rows,
    write_artifacts,
)
from research.existing_architecture_exhaustion_review_v1.spec import canonical_spec, source_sha256, spec_sha256

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
        "STRESS_NEW_METRIC_N": 0,
        "STRESS_NEW_REPLAY_N": 0,
        "FUTURE_DATA_N": 0,
        "NEW_REPLAY": False,
        "NEW_PNL_SIMULATION": False,
        "NEW_CANDIDATE": False,
        "STANDALONE_OPEN_STRENGTH": False,
        "SIZING": False,
    }


def _publish(report: dict[str, Any]) -> None:
    d = dict(report.get("decision") or {})
    inv = list(d.get("inventory") or [])
    report["answers"] = build_answers(d)
    report["_markdown"] = build_markdown(
        {
            "answers": report["answers"],
            "decision": d,
            "inventory": inv,
        }
    )
    remaining = [
        {
            **{k: r.get(k) for k in ROW_FIELDS},
            "REMAINING_ELIGIBLE": True,
        }
        for r in inv
        if remaining_eligible(r)
    ]
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items()}),
        "architecture_inventory": [{k: r.get(k) for k in ROW_FIELDS} for r in inv],
        "duplicate_map": duplicate_rows(inv),
        "open_strength_audit": kv_rows(d.get("open_strength_audit")),
        "x6r3_audit": kv_rows(d.get("x6r3_audit")),
        "closure_evidence": closure_rows(inv),
        "remaining_candidates": remaining or [{"ARCHITECTURE_ID": "NONE", "REMAINING_ELIGIBLE": False}],
        "decision": kv_rows(
            {
                "CASE": d.get("CASE"),
                "CASE_NAME": d.get("CASE_NAME"),
                "VERDICT": d.get("VERDICT"),
                "NEXT": d.get("NEXT"),
                "SELECTED_NEXT_ARCHITECTURE": d.get("SELECTED_NEXT_ARCHITECTURE"),
                "PNL_USED_TO_SELECT_NEXT": False,
                "remaining_eligible_ids": d.get("remaining_eligible_ids"),
                "priority_order": d.get("priority_order"),
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
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": source_sha256(),
        "decision": pack,
        "inventory": pack["inventory"],
        "counts": pack["counts"],
        "open_strength_audit": pack["open_strength_audit"],
        "x6r3_audit": pack["x6r3_audit"],
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
    print(f"REMAINING {pack['remaining_eligible_ids']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
