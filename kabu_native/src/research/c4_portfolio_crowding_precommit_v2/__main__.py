"""Offline C4 V2 semantic correction + intervention prune. No economics. No Stress."""
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
from research.c4_portfolio_crowding_precommit_v2 import ANALYSIS_ID
from research.c4_portfolio_crowding_precommit_v2.analyze import build_answers, decide
from research.c4_portfolio_crowding_precommit_v2.isolation import (
    EXIT_SOURCE_FILE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.c4_portfolio_crowding_precommit_v2.prior import artifacts_modified, snapshot_prior_artifacts
from research.c4_portfolio_crowding_precommit_v2.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.c4_portfolio_crowding_precommit_v2.spec import canonical_spec, file_sha256, source_sha256, spec_sha256

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
        "CONTROL_ECONOMICS_RUN": False,
        "TREATMENT_ECONOMICS_RUN": False,
        "FILL_COUNT_COMPUTED": False,
        "TRADE_COUNT_COMPUTED": False,
        "PNL_COMPUTED": False,
        "PF_COMPUTED": False,
        "MAXDD_COMPUTED": False,
        "RANKING_COMPUTED": False,
        "FOLD_ECONOMICS_RUN": False,
        "SIZING": False,
        "NEW_EXIT_CREATED": False,
        "EXIT6_CREATED": False,
        "OCCUPANCY_K_SEARCH": False,
        "PRIOR_ARTIFACT_MODIFIED": False,
    }


def _arm_public(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "CANDIDATE_ID",
        "ARM",
        "ENTRY_ID",
        "C4_POLICY",
        "EXECUTION",
        "EXIT",
        "CAP",
        "same_symbol",
        "occupancy",
        "slot_release",
        "reentry",
        "WINNER_ELIGIBLE",
        "BACKFILL",
    )
    return {k: row.get(k) for k in keys}


def _publish(report: dict[str, Any]) -> None:
    d = dict(report.get("decision") or {})
    report["answers"] = build_answers(d)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    prior = dict(d.get("prior") or {})
    streams = dict(d.get("streams") or {})
    order = dict(d.get("order") or {})
    lib = dict(d.get("library") or {})
    inter = dict(d.get("intervention") or {})
    rows = list(d.get("intervention_rows") or [])
    sheets = {
        "answers": kv_rows({k: v for k, v in (report.get("answers") or {}).items()}),
        "prior_v1": kv_rows(
            {
                "ANALYSIS_ID": prior.get("ANALYSIS_ID"),
                "VERDICT": prior.get("VERDICT"),
                "C4_POLICY_ID": prior.get("C4_POLICY_ID"),
                "C4_POLICY_SHA256": prior.get("C4_POLICY_SHA256"),
                "ENTRY_N": prior.get("ENTRY_N"),
                "EXIT_N": prior.get("EXIT_N"),
                "CONTROL_ARM_N": prior.get("CONTROL_ARM_N"),
                "TREATMENT_ARM_N": prior.get("TREATMENT_ARM_N"),
                "TOTAL_ARM_N": prior.get("TOTAL_ARM_N"),
                "CANDIDATE_ECONOMICS_RUN": False,
                "ok": prior.get("ok"),
            }
        ),
        "policy_v2": kv_rows(dict(d.get("policy") or {})),
        "canonical_order": kv_rows(order),
        "raw_streams": list(streams.get("raw_streams") or [{"empty": True}]),
        "v1_v2_compare": list(streams.get("compare") or [{"empty": True}]),
        "c4_streams": list(streams.get("c4_streams") or [{"empty": True}]),
        "intervention": rows or [{"empty": True}],
        "block_intervention": list(d.get("block_intervention") or [{"empty": True}]),
        "attribution_eligibility": kv_rows(
            {
                "NO_INTERVENTION_ENTRY_N": inter.get("NO_INTERVENTION_ENTRY_N"),
                "ACTIVE_ENTRY_N": inter.get("ACTIVE_ENTRY_N"),
                "C4_ATTRIBUTION_ELIGIBLE_ENTRY_N": inter.get("C4_ATTRIBUTION_ELIGIBLE_ENTRY_N"),
                "C4_ATTRIBUTION_INELIGIBLE_ENTRY_N": inter.get("C4_ATTRIBUTION_INELIGIBLE_ENTRY_N"),
                "eligible_ids": inter.get("eligible_ids"),
                "ineligible_ids": inter.get("ineligible_ids"),
                "MIN_INFORMATIVE_BLOCK_N": inter.get("MIN_INFORMATIVE_BLOCK_N"),
                "BACKFILL": False,
            }
        ),
        "matched_controls": [_arm_public(r) for r in list(d.get("controls") or [])] or [{"empty": True}],
        "matched_treatments": [_arm_public(r) for r in list(d.get("treatments") or [])] or [{"empty": True}],
        "coverage_gates": kv_rows(dict(d.get("coverage_gates") or {})),
        "economic_gates": kv_rows(dict(d.get("economic_gates") or {})),
        "incremental_gates": kv_rows(dict(d.get("incremental_gate") or {})),
        "strategy_stability": kv_rows(dict(d.get("stability_gates") or {})),
        "attribution_stability": kv_rows(dict(d.get("attribution_stability") or {})),
        "folds": kv_rows(dict(d.get("folds") or {})),
        "canary": kv_rows(dict(d.get("canary") or {})),
        "hashes": kv_rows(dict(d.get("hashes") or {})),
        "safety": kv_rows(report.get("safety") or _safety()),
        "decision": kv_rows(
            {
                "CASE": d.get("CASE"),
                "CASE_NAME": d.get("CASE_NAME"),
                "VERDICT": d.get("VERDICT"),
                "NEXT": d.get("NEXT"),
                "ELIGIBLE_ENTRY_N": lib.get("ELIGIBLE_ENTRY_N"),
                "ELIGIBLE_TOTAL_ARM_N": lib.get("ELIGIBLE_TOTAL_ARM_N"),
                "EVERY_TREATMENT_HAS_MATCHED_CONTROL": lib.get("EVERY_TREATMENT_HAS_MATCHED_CONTROL"),
                "CONTROL_ELIGIBLE_AS_WINNER": False,
                "C4_POLICY_HASH_FROZEN_BEFORE_COUNTS": d.get("C4_POLICY_HASH_FROZEN_BEFORE_COUNTS"),
                "RAW_STREAM_REPRODUCTION_PASS": streams.get("RAW_STREAM_REPRODUCTION_PASS"),
                "C4_STREAM_INVARIANCE_PASS": streams.get("C4_STREAM_INVARIANCE_PASS"),
                "CANDIDATE_ECONOMICS_RUN": False,
                "TRUE_OOS": False,
                "CERTIFIED": False,
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
    safety = _safety()
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": source_sha256(),
        "decision": pack,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "PRIOR_ARTIFACT_MODIFIED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _publish(report)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"CASE {pack['CASE_NAME']}", flush=True)
    print(f"VERDICT {pack['VERDICT']}", flush=True)
    print(f"NEXT {pack['NEXT']}", flush=True)
    print(f"ELIGIBLE_ENTRY_N {(pack.get('intervention') or {}).get('C4_ATTRIBUTION_ELIGIBLE_ENTRY_N')}", flush=True)
    print(f"ELIGIBLE_TOTAL_ARM_N {(pack.get('library') or {}).get('ELIGIBLE_TOTAL_ARM_N')}", flush=True)
    print(f"ECONOMICS {pack.get('CANDIDATE_ECONOMICS_RUN')}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
