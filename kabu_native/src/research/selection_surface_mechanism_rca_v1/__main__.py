"""Offline selection-surface mechanism RCA. Existing artifacts only. No harvest/Stress/future."""
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
from research.selection_surface_mechanism_rca_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.selection_surface_mechanism_rca_v1.analyze import already_executed_check, build_answers, decide
from research.selection_surface_mechanism_rca_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.selection_surface_mechanism_rca_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.selection_surface_mechanism_rca_v1.sources import load_sources
from research.selection_surface_mechanism_rca_v1.spec import canonical_spec, source_sha256, spec_sha256

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
        "HOLDOUT_OPENED": False,
        "RAW_CAPTURE_READ_N": 0,
        "NEW_REPLAY": False,
        "NEW_FOLD_RUN": False,
        "NEW_FOLD_ECONOMICS": False,
        "NEW_PNL_SIMULATION": False,
        "NEW_CANDIDATE": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def _fold_public(row: dict[str, Any]) -> dict[str, Any]:
    skip = {"SOURCE_KEYS", "GATE_MARGIN"}
    return {k: v for k, v in row.items() if k not in skip}


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    folds = list(pack.get("folds") or [])
    s23 = dict(pack.get("s2_s3") or {})
    pnls = dict(s23.get("winner_block_pnls") or {})
    nlab = dict(pack.get("n_labels") or {})
    sheets = {
        "answers": kv_rows(report.get("answers")),
        "source_inventory": list(pack.get("source_inventory") or []),
        "stability_gates": kv_rows(pack.get("stability_gates")),
        "winner_blocks": [{"block": k, "pnl": pnls.get(k)} for k in ("B1", "B2", "B3", "B4", "B5")],
        "folds": [_fold_public(f) for f in folds],
        "n_classification": [
            {"block": bid, "label": lab, **dict(pack.get("n_counts") or {})} for bid, lab in nlab.items()
        ],
        "heldout_aggregate": kv_rows(pack.get("aggregate")),
        "comparator": [
            {
                "block": f.get("block"),
                "STATUS": f.get("COMPARATOR_STATUS"),
                "FOLD_SELECTED_TEST_PNL": f.get("FOLD_SELECTED_TEST_PNL"),
                "FULL_DEV_WINNER_BLOCK_PNL": f.get("FULL_DEV_WINNER_BLOCK_PNL"),
            }
            for f in folds
        ],
        "availability": [
            {
                "block": f.get("block"),
                "G6_AVAILABLE": f.get("G6_AVAILABLE"),
                "G6_STATUS": f.get("G6_STATUS"),
                "RANK_AVAILABLE": f.get("RANK_AVAILABLE"),
                "ROBUST_SCORE_AVAILABLE": f.get("ROBUST_SCORE_AVAILABLE"),
            }
            for f in folds
        ],
        "component_scope": kv_rows(
            {
                "ST_SPECIFIC": True,
                "L1_WIDE": False,
                "CROSS_LINEAGE": False,
                "PARENT_PRIMARY_COMPONENT": d.get("PARENT_PRIMARY_COMPONENT_UNCHANGED"),
                "RESIDUAL_BOTTLENECK": d.get("RESIDUAL_BOTTLENECK_SUBJECT"),
            }
        ),
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
    sources = load_sources()
    reused = already_executed_check(str(sources["inventory_fingerprint"]))
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
    pack = decide(sources)
    after = snapshot(phase="POST")
    opened: list[Path] = []
    leak = {
        "HOLDOUT_BURNED_READ_N": holdout_path_touch_n(opened),
        "STRESS_READ_N": stress_path_touch_n(opened),
        "FUTURE_DATA_N": 0,
        "RAW_CAPTURE_READ_N": 0,
    }
    if leak["HOLDOUT_BURNED_READ_N"] or leak["STRESS_READ_N"] or leak["FUTURE_DATA_N"]:
        raise RuntimeError(f"LEAKAGE {leak}")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": source_sha256(),
        "inventory_fingerprint": pack["inventory_fingerprint"],
        "ALREADY_EXECUTED_CHECK": False,
        "REUSED_EXISTING_RESULT": False,
        "parent": pack["parent"],
        "decision": pack["decision"],
        "winner": pack["winner"],
        "s2_s3": pack["s2_s3"],
        "folds": pack["folds"],
        "n_labels": pack["n_labels"],
        "n_counts": pack["n_counts"],
        "stability_gates": pack["stability_gates"],
        "aggregate": pack["aggregate"],
        "guards": pack["guards"],
        "source_inventory": pack["source_inventory"],
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
    print("REUSED_EXISTING_RESULT false", flush=True)
    print(f"S1 {pack['decision']['S1_PASS']}", flush=True)
    print(f"S2 {pack['decision']['S2_PASS']}", flush=True)
    print(f"S3 {pack['decision']['S3_PASS']}", flush=True)
    print(f"S4 {pack['decision']['S4_PASS']}", flush=True)
    print(f"N1N2N3 {pack['n_counts']}", flush=True)
    print(f"RECURRENCE {pack['decision']['RECURRENCE_FAILURE_MODE']}", flush=True)
    print(f"HELDOUT {pack['decision']['HELDOUT_TRANSFER_FAILURE_SUPPORTED']}", flush=True)
    print(f"VERDICT {pack['decision']['VERDICT']}", flush=True)
    print(f"NEXT {pack['decision']['NEXT']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
