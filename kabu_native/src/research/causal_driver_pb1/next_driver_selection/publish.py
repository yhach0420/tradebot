"""Write report.json / report.md / audit.xlsx only under next_driver_selection_v1."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.next_driver_selection import ANALYSIS_ID, PROGRAM_ID
from research.causal_driver_pb1.next_driver_selection.isolation import OUT

SHEETS = (
    "Manifest",
    "USDJPY_Closeout",
    "Shared_Response_Coverage",
    "Coverage_RCA",
    "Driver_Inventory",
    "Futures",
    "Leader_Laggard",
    "Sector_Breadth",
    "Global_External",
    "Selection_Matrix",
    "Architecture_Compatibility",
    "Safety",
)


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900")


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and type(x).__module__.startswith("numpy"):
        return _clean(x.item())
    if isinstance(x, float) and x != x:
        return None
    return x


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame([{"empty": True}])
    flat = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if isinstance(v, (dict, list, tuple)):
                row[k] = json.dumps(_clean(v), ensure_ascii=False)
            else:
                row[k] = v
        flat.append(row)
    return pd.DataFrame(flat)


def _markdown(report: dict[str, Any]) -> str:
    a = report["answers"]
    return "\n".join(
        [
            f"# {PROGRAM_ID} / {ANALYSIS_ID}",
            "",
            f"**VERDICT:** `{a.get('VERDICT')}`",
            "",
            f"**NEXT:** `{a.get('NEXT')}`",
            "",
            f"- USDJPY_DRIVER_FAMILY_STATUS `{a.get('USDJPY_DRIVER_FAMILY_STATUS')}`",
            f"- stock_target_missing_rate `{a.get('stock_target_missing_rate')}`",
            f"- reconstructed_missing_rate `{a.get('reconstructed_missing_rate')}`",
            f"- shared_response_harness_valid `{a.get('shared_response_harness_valid')}`",
            f"- INDEX_FUTURES ready `{a.get('index_futures_ready')}`",
            f"- LEADER_LAGGARD ready `{a.get('leader_laggard_ready')}`",
            f"- PRIMARY_NEXT_DRIVER `{a.get('PRIMARY_NEXT_DRIVER')}`",
            f"- BACKUP_DRIVER `{a.get('BACKUP_DRIVER')}`",
            f"- USDJPY_REOPENED `{a.get('USDJPY_REOPENED')}`",
            f"- FROZEN_VALIDATION_ECONOMIC_OPENED `{a.get('FROZEN_VALIDATION_ECONOMIC_OPENED')}`",
            f"- PROSPECTIVE_DATA_OPENED `{a.get('PROSPECTIVE_DATA_OPENED')}`",
            "",
            "No future-return search. Phase 3 / next discovery precommit is not started.",
            "",
        ]
    )


def publish(*, evaluation: dict[str, Any], safety: dict[str, Any], isolation_pre: dict[str, Any], isolation_post: dict[str, Any]) -> dict[str, Any]:
    rca = evaluation.get("rca") or {}
    fut = evaluation.get("futures") or {}
    sel = evaluation.get("selection") or {}
    close = evaluation.get("closeout") or {}
    answers = {
        "VERDICT": evaluation.get("VERDICT"),
        "NEXT": evaluation.get("NEXT"),
        "USDJPY_DRIVER_FAMILY_STATUS": close.get("USDJPY_DRIVER_FAMILY_STATUS"),
        "precommit_sha256": evaluation.get("precommit_sha256"),
        "stock_target_missing_rate": evaluation.get("published_stock_target_missing_rate"),
        "reconstructed_missing_rate": rca.get("phase2_mkt105_h5_missing_rate_reconstructed"),
        "shared_response_harness_valid": bool(rca.get("ok")) and not (rca.get("contract_gap") or {}).get("shared_harness_blocks_next_driver"),
        "index_futures_ready": bool(fut.get("ready_as_next_driver")),
        "leader_laggard_ready": bool((evaluation.get("leader_laggard") or {}).get("ready")),
        "PRIMARY_NEXT_DRIVER": evaluation.get("PRIMARY_NEXT_DRIVER"),
        "BACKUP_DRIVER": evaluation.get("BACKUP_DRIVER"),
        "why": evaluation.get("why"),
        "USDJPY_REOPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "C1_USDJPY_OUTCOMES_OPENED": False,
        "future_return_search": False,
        "new_driver_family_id": sel.get("new_driver_family_id"),
        "phase0_enum_mutated": False,
        "blockers": evaluation.get("blockers"),
    }
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": _now(),
        "answers": answers,
        "evaluation": _clean(evaluation),
        "safety": safety,
        "isolation_pre": isolation_pre,
        "isolation_post": isolation_post,
    }
    # Drop bulky numpy-adjacent keep masks if any slipped through
    ev = report["evaluation"]
    if isinstance(ev.get("rca"), dict):
        ev["rca"] = {k: v for k, v in ev["rca"].items() if k not in {"mkt_counts", "exact_keep", "asof_keep"}}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")

    gap = rca.get("contract_gap") or {}
    with pd.ExcelWriter(OUT / "audit.xlsx", engine="openpyxl") as xw:
        _df(
            [
                {
                    "program": PROGRAM_ID,
                    "analysis": ANALYSIS_ID,
                    "verdict": answers["VERDICT"],
                    "next": answers["NEXT"],
                    "created_at": _now(),
                }
            ]
        ).to_excel(xw, sheet_name="Manifest", index=False)
        _df([close]).to_excel(xw, sheet_name="USDJPY_Closeout", index=False)
        _df(list(rca.get("symbol_rows") or [])).to_excel(xw, sheet_name="Shared_Response_Coverage", index=False)
        _df(
            [
                {
                    "published_missing": evaluation.get("published_stock_target_missing_rate"),
                    "reconstructed_missing": rca.get("phase2_mkt105_h5_missing_rate_reconstructed"),
                    "match": rca.get("phase2_mkt105_h5_missing_rate_match"),
                    "dev_exact_usable": rca.get("dev_exact_usable_rate"),
                    "dev_asof_usable": rca.get("dev_asof_usable_rate"),
                    "c1_exact_usable": rca.get("c1_exact_usable_rate"),
                    "c1_asof_usable": rca.get("c1_asof_usable_rate"),
                    "label_counts": rca.get("dev_label_counts"),
                    "label_counts_last_completed": rca.get("dev_label_counts_if_last_completed"),
                    "source_hole_dev_pair_n": rca.get("source_hole_dev_pair_n"),
                    "not_listed_dev_pair_n": rca.get("not_listed_dev_pair_n"),
                    "newly_listed_symbol_n": rca.get("newly_listed_symbol_n"),
                    "per_date_exact": rca.get("per_date_exact_valid_n"),
                    "per_clock_exact": rca.get("per_clock_exact_valid_n"),
                    "per_date_asof": rca.get("per_date_asof_valid_n"),
                    "per_clock_asof": rca.get("per_clock_asof_valid_n"),
                    "contract_expected": gap.get("contract_expected_behavior"),
                    "actual_behavior": gap.get("actual_behavior"),
                    "exact_code_defect": gap.get("exact_code_defect"),
                    "affected_observation_count": gap.get("affected_observation_count"),
                    "affected_clock_fraction": gap.get("affected_clock_fraction"),
                    "independent_of_outcomes": gap.get("why_independent_of_outcome_values"),
                    "missing_filled_as_zero": gap.get("missing_filled_as_zero"),
                    "would_invalidate_phase2": gap.get("would_invalidate_phase2_not_found"),
                    "harness_blocks_next": gap.get("shared_harness_blocks_next_driver"),
                    "reason_not_invalidated": gap.get("reason_not_invalidated"),
                    "future_returns_computed": rca.get("future_returns_computed"),
                    "c1_usdjpy_outcomes_opened": rca.get("c1_usdjpy_outcomes_opened"),
                    "sector_rows": rca.get("sector_rows"),
                    "per_date_exact_hist": rca.get("per_date_exact_hist"),
                    "per_clock_exact_hist": rca.get("per_clock_exact_hist"),
                }
            ]
        ).to_excel(xw, sheet_name="Coverage_RCA", index=False)
        _df(list(evaluation.get("inventory") or [])).to_excel(xw, sheet_name="Driver_Inventory", index=False)
        _df([fut]).to_excel(xw, sheet_name="Futures", index=False)
        _df([evaluation.get("leader_laggard") or {}]).to_excel(xw, sheet_name="Leader_Laggard", index=False)
        _df([evaluation.get("sector_breadth") or {}]).to_excel(xw, sheet_name="Sector_Breadth", index=False)
        _df([evaluation.get("global_external") or {}]).to_excel(xw, sheet_name="Global_External", index=False)
        _df(list(sel.get("rows") or [])).to_excel(xw, sheet_name="Selection_Matrix", index=False)
        _df(
            [
                {
                    "new_driver_family_id": sel.get("new_driver_family_id"),
                    "phase0_enum_mutated": False,
                    "not_embedded_in_pb1": True,
                    "v4_unchanged": True,
                    "complete_strategy_unchanged": True,
                    "runtime_slots": (sel.get("runtime") or {}).get("kabu_slot_n"),
                    "frozen_leader_set_max_n": (sel.get("runtime") or {}).get("frozen_leader_set_max_n"),
                    "fits_50": (sel.get("runtime") or {}).get("leader_plus_candidates_fits_50"),
                    "leave_target_out": True,
                }
            ]
        ).to_excel(xw, sheet_name="Architecture_Compatibility", index=False)
        _df([safety]).to_excel(xw, sheet_name="Safety", index=False)

    return {"ok": True, "out": str(OUT), "sheets": list(SHEETS)}
