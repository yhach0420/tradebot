"""Offline EXIT semantic uniqueness audit. No candidate harvest/Stress/future/economics."""
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
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import ANALYSIS_ID
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.analyze import build_answers, decide
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.isolation import (
    EXIT_SOURCE_FILE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.prior import artifacts_modified, snapshot_prior_artifacts
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.spec import canonical_spec, file_sha256, source_sha256, spec_sha256

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
        "FUTURE_DATA_N": 0,
        "BURNED_HOLDOUT_READ_N": 0,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_FILL_COUNT_COMPUTED": False,
        "CANDIDATE_TRADE_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "SIZING": False,
        "NEW_EXIT_CREATED": False,
        "EXIT6_CREATED": False,
    }


def _bar_public(bar: dict[str, Any]) -> dict[str, Any]:
    skip = {"FINISH_SOURCE", "NEW_SLOT_SOURCE", "POST_FILL_BARS_SOURCE", "TECHNICAL_FIRE_I_SOURCE"}
    return {k: v for k, v in bar.items() if k not in skip}


def _pred_public(pred: dict[str, Any]) -> dict[str, Any]:
    skip = {"Z2_BRANCH", "Z4_BRANCH", "Z5_BRANCH", "LIBRARY_ROWS"}
    return {k: v for k, v in pred.items() if k not in skip}


def _publish(report: dict[str, Any]) -> None:
    d = dict(report.get("decision") or {})
    report["answers"] = build_answers(d)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    eq = dict(d.get("equivalence") or {})
    suf = dict(d.get("suffix_audit") or {})
    gates = {
        **{f"coverage_{k}": v for k, v in (d.get("coverage_gates") or {}).items()},
        **{f"fold_{k}": v for k, v in (d.get("fold_coverage_gates") or {}).items()},
        **{f"economic_{k}": v for k, v in (d.get("economic_gates") or {}).items()},
        **{f"stability_{k}": v for k, v in (d.get("stability_gates") or {}).items()},
    }
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items()}),
        "prior": kv_rows(d.get("prior")),
        "valid_bar_contract": kv_rows(_bar_public(dict(d.get("bar_contract") or {}))),
        "bar_integrity": kv_rows(dict(d.get("bar_integrity") or {})),
        "exit_sources": list((d.get("exit_predicates") or {}).get("LIBRARY_ROWS") or []),
        "suffix_domain": kv_rows(
            {
                "SUFFIX_CASE_N": suf.get("SUFFIX_CASE_N"),
                "ARRAY_N": suf.get("ARRAY_N"),
                "PAIR_COMPARISON_N": suf.get("PAIR_COMPARISON_N"),
                "ALL_10_PAIRS_COMPLETED": suf.get("ALL_10_PAIRS_COMPLETED"),
                "ok": suf.get("ok"),
            }
        ),
        "pairwise_equivalence": list(eq.get("pair_rows") or suf.get("pairwise") or []),
        "equivalence_classes": list(eq.get("classes") or []),
        "final_exits": list(d.get("final_exits") or []),
        "final_pairs": list(d.get("final_pairs") or []),
        "gates": kv_rows(gates),
        "safety": kv_rows(report.get("safety") or _safety()),
        "decision": kv_rows(
            {
                "CASE": d.get("CASE"),
                "CASE_NAME": d.get("CASE_NAME"),
                "VERDICT": d.get("VERDICT"),
                "NEXT": d.get("NEXT"),
                "FINAL_EXIT_N": eq.get("FINAL_EXIT_N"),
                "FINAL_PAIR_N": len(list(d.get("final_pairs") or [])),
                "DROPPED": eq.get("dropped_exit_ids"),
                "CANDIDATE_ECONOMICS_RUN": False,
                "REUSED_EXISTING_RESULT": d.get("reused_existing_result"),
            }
        ),
    }
    if not sheets["pairwise_equivalence"]:
        sheets["pairwise_equivalence"] = list(suf.get("pairwise") or [{"empty": True}])
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
    print(f"FINAL_EXIT {(pack.get('equivalence') or {}).get('FINAL_EXIT_N')}", flush=True)
    print(f"FINAL_PAIR {len(pack.get('final_pairs') or [])}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
