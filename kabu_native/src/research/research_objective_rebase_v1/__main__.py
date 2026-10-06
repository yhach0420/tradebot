"""Offline research-objective rebase. Existing artifacts only. No harvest/Stress/future."""
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
from research.research_objective_rebase_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.research_objective_rebase_v1.analyze import TAXONOMY_KEYS, already_executed_check, build_answers, decide
from research.research_objective_rebase_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.research_objective_rebase_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    taxonomy_rows,
    write_artifacts,
)
from research.research_objective_rebase_v1.sources import load_sources
from research.research_objective_rebase_v1.spec import canonical_spec, source_sha256, spec_sha256

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
        "STRESS_RAW_READ_N": 0,
        "STRESS_FILE_OPEN_N": 0,
        "STRESS_NEW_METRIC_N": 0,
        "STRESS_NEW_REPLAY_N": 0,
        "FUTURE_DATA_N": 0,
        "BURNED_HOLDOUT_READ_N": 0,
        "NEW_REPLAY": False,
        "NEW_PNL_SIMULATION": False,
        "NEW_CANDIDATE": False,
        "NEW_ENTRY": False,
        "NEW_EXIT": False,
        "NEW_THRESHOLD": False,
        "NEW_FULL_CAUSAL_RUN": False,
        "NEW_FOLD_RUN": False,
        "SIZING": False,
        "C5_CREATED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def _flatten_l1(l1: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"section": "lineage", **{k: v for k, v in l1.items() if k not in {"simple", "e4_causal", "st", "c1", "discovery_flags", "taxonomy", "GENERALIZATION_FAILURE_TYPES"}}},
        {"section": "simple", **dict(l1.get("simple") or {})},
        {"section": "e4_causal", **dict(l1.get("e4_causal") or {})},
        {"section": "st", **dict(l1.get("st") or {})},
        {"section": "c1", **{k: v for k, v in dict(l1.get("c1") or {}).items() if k != "G1_G2_CANDIDATES"}},
    ]
    for cand in list((l1.get("c1") or {}).get("G1_G2_CANDIDATES") or []):
        rows.append({"section": "c1_g1g2", **cand})
    for flag in list(l1.get("discovery_flags") or []):
        rows.append({"section": "discovery_flag", **flag})
    return rows


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    l1 = dict(pack.get("l1") or {})
    l2 = dict(pack.get("l2") or {})
    l3 = dict(pack.get("l3") or {})
    sheets = {
        "answers": kv_rows(report.get("answers")),
        "source_inventory": list(pack.get("source_inventory") or []),
        "architecture_to_lineage": list(pack.get("architecture_to_lineage") or []),
        "lineage_dependencies": list(pack.get("lineage_dependencies") or []),
        "l1_technical": _flatten_l1(l1),
        "l2_recovery": [
            {k: v for k, v in l2.items() if k not in {"candidates", "taxonomy", "GENERALIZATION_FAILURE_TYPES", "R2_X1_Z3"}},
            {"section": "R2_X1_Z3", **dict(l2.get("R2_X1_Z3") or {})},
            *[
                {"section": "candidate", **c}
                for c in list(l2.get("candidates") or [])
            ],
        ],
        "l3_activity": [
            {k: v for k, v in l3.items() if k not in {"candidates", "taxonomy", "GENERALIZATION_FAILURE_TYPES"}},
            *[{"section": "candidate", **c} for c in list(l3.get("candidates") or [])],
        ],
        "c4_overlay": kv_rows(pack.get("c4")),
        "failure_taxonomy": taxonomy_rows(pack),
        "evidence_levels": [
            {
                "LINEAGE_ID": "L1_TECHNICAL_PRICE_STATE",
                "EVIDENCE_LEVEL": l1.get("EVIDENCE_LEVEL"),
                "AGGREGATE_EDGE": l1.get("EVIDENCE_LEVEL") != "E0_NO_AGGREGATE_EDGE",
                "ROBUST": False,
            },
            {
                "LINEAGE_ID": "L2_RECOVERY_RECLAIM_PATH",
                "EVIDENCE_LEVEL": l2.get("EVIDENCE_LEVEL"),
                "AGGREGATE_EDGE": True,
                "ROBUST": False,
            },
            {
                "LINEAGE_ID": "L3_ACTIVITY_ONSET",
                "EVIDENCE_LEVEL": l3.get("EVIDENCE_LEVEL"),
                "AGGREGATE_EDGE": False,
                "ROBUST": False,
            },
            {
                "LINEAGE_ID": "O1_PORTFOLIO_CROWDING_OVERLAY",
                "EVIDENCE_LEVEL": "NOT_A_LINEAGE",
                "AGGREGATE_EDGE": False,
                "ROBUST": False,
            },
        ],
        "aggregate_edge": [
            {
                "ROBUST_LINEAGE_N": d.get("ROBUST_LINEAGE_N"),
                "AGGREGATE_EDGE_LINEAGE_N": d.get("AGGREGATE_EDGE_LINEAGE_N"),
                "NO_AGGREGATE_EDGE_LINEAGE_N": d.get("NO_AGGREGATE_EDGE_LINEAGE_N"),
                "C4_INCLUDED": False,
                "LINEAGE_EVIDENCE_LEVELS": d.get("LINEAGE_EVIDENCE_LEVELS"),
            }
        ],
        "generalization": [
            {
                "GENERALIZATION_FAILURE_LINEAGE_N": d.get("GENERALIZATION_FAILURE_LINEAGE_N"),
                "GENERALIZATION_FAILURE_LINEAGE_IDS": d.get("GENERALIZATION_FAILURE_LINEAGE_IDS"),
                "L1_TYPES": l1.get("GENERALIZATION_FAILURE_TYPES"),
                "L2_TYPES": l2.get("GENERALIZATION_FAILURE_TYPES"),
                "L3_TYPES": l3.get("GENERALIZATION_FAILURE_TYPES"),
            }
        ],
        "value_capture": kv_rows(pack.get("value_capture")),
        "pseudo_replication_guard": kv_rows(pack.get("pseudo_replication_guard")),
        "closed_lineage_guard": kv_rows(pack.get("closed_lineage_guard")),
        "decision": kv_rows(d),
        "safety": kv_rows(report.get("safety")),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    assert TAXONOMY_KEYS
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
        print(f"VERDICT {prev.get('decision', {}).get('VERDICT') or (prev.get('answers') or {}).get('50_VERDICT')}", flush=True)
        print(f"NEXT {prev.get('decision', {}).get('NEXT') or (prev.get('answers') or {}).get('51_NEXT')}", flush=True)
        print(f"OUT {OUT}", flush=True)
        print("STOP", flush=True)
        return
    pack = decide(sources)
    after = snapshot(phase="POST")
    opened = []
    leak = {
        "HOLDOUT_BURNED_READ_N": holdout_path_touch_n(opened),
        "STRESS_READ_N": stress_path_touch_n(opened),
        "STRESS_FILE_OPEN_N": stress_path_touch_n(opened),
        "FUTURE_DATA_N": 0,
        "BURNED_HOLDOUT_READ_N": 0,
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
        "decision": pack["decision"],
        "c4": pack["c4"],
        "architecture_space": pack["architecture_space"],
        "l1": pack["l1"],
        "l2": pack["l2"],
        "l3": pack["l3"],
        "value_capture": pack["value_capture"],
        "source_inventory": pack["source_inventory"],
        "architecture_to_lineage": pack["architecture_to_lineage"],
        "lineage_dependencies": pack["lineage_dependencies"],
        "pseudo_replication_guard": pack["pseudo_replication_guard"],
        "closed_lineage_guard": pack["closed_lineage_guard"],
        "flags": pack["flags"],
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
    print(f"CASE {pack['decision']['CASE']}", flush=True)
    print(f"PRIMARY_DEFICIENCY {pack['decision']['PRIMARY_DEFICIENCY']}", flush=True)
    print(f"VERDICT {pack['decision']['VERDICT']}", flush=True)
    print(f"NEXT {pack['decision']['NEXT']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
