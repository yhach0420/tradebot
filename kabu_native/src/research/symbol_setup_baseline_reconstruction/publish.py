"""Publish the reconstruction. Does not run a strategy."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.causal_driver_pb1.sector_state_alpha_complete_precommit.contract import component_identities
from research.symbol_setup_baseline_reconstruction import (
    ANALYSIS_ID,
    CASE_GAPS,
    CASE_UNRESOLVED,
    NEXT_GAPS,
    NEXT_UNRESOLVED,
)
from research.symbol_setup_baseline_reconstruction.contract import contract, stages, versions
from research.symbol_setup_baseline_reconstruction.isolation import OUT, write_overlap_n


def _put(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        ws.append(["none"])
        return
    keys: list[str] = []
    flat = []
    for row in rows:
        item = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, sort_keys=True)
            item[str(k)] = v
            if str(k) not in keys:
                keys.append(str(k))
        flat.append(item)
    ws.append(keys)
    for item in flat:
        ws.append([item.get(k) for k in keys])


def build() -> dict[str, Any]:
    frozen = contract()
    ids = component_identities()
    if not frozen["identity_resolved"]:
        verdict, nxt = CASE_UNRESOLVED, NEXT_UNRESOLVED
    else:
        verdict, nxt = CASE_GAPS, NEXT_GAPS
    stage_rows = stages(frozen["source_file_sha256"])
    reuse = [
        {"id": "SIMPLE_TECH_V1_PORTFOLIO_REPLAY", "sha256": frozen["source_file_sha256"]["portfolio.py"], "source_file": "src/research/simple_tech_entry_family/portfolio.py", "status": "V1_OWN_CAP_SAME_SYMBOL_SLOT_RELEASE"},
        {"id": ids["occupancy"]["OCCUPANCY_ENGINE_ID"], "sha256": ids["occupancy"]["OCCUPANCY_ENGINE_SHA256"], "source_file": ids["occupancy"]["source_file"], "status": "REUSABLE_LATER_NOT_THE_V1_FILL_ENGINE"},
        {"id": "HISTORICAL_NEXT_BAR_OPEN_EXECUTION", "sha256": ids["ECONOMIC_REPLAY_RUNNER_SHA256"], "source_file": "pb1 complete-strategy fill/clocks, runner bundle", "status": "AVAILABLE_NOT_THE_V1_EXECUTION"},
        {"id": frozen["exit_id"], "sha256": frozen["exit_candidate_sha256"], "source_file": "src/small_paper/v1r_exit_v2_contract.py", "status": "V1_EXIT_NOT_THESIS_ALIGNED"},
    ]
    return {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "contract": frozen,
        "versions": versions(),
        "stages": stage_rows,
        "portfolio_reuse": reuse,
        "missing_contracts": [
            "ENTRY_ALIGNED_EXIT",
            "SEPARATE_TRIGGER_TIMEFRAME",
            "SUPPORT_RESISTANCE_STRUCTURE_LAYER",
        ],
        "v2_compatibility": {
            "symbol_first": True,
            "context_independent": True,
            "one_minute_mixed_setup_and_trigger": True,
            "board_is_support_veto": True,
            "exit_thesis_aligned": False,
            "pb1_required": False,
            "market_context_required": False,
            "sector_context_required": False,
        },
        "new_pnl_run": False,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "v4_changed": False,
        "v5_created": False,
        "pb1_changed": False,
    }


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    c = report["contract"]
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        f"Baseline: {c['baseline_id']}",
        f"Spec SHA256: {c['baseline_spec_sha256']}",
        "",
        "The V1 identity is recovered from source. The exit is legacy C14 Arch E, not an invalidation of the symbol setup.",
        "Setup and trigger are both completed 1-minute bars. Execution is a 5-second passive bid.",
        "No new economic result was calculated.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    by_id = {row["stage_id"]: row for row in report["stages"]}
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"analysis_id": ANALYSIS_ID, "verdict": report["verdict"], "next": report["next"], "baseline_id": c["baseline_id"], "baseline_sha256": c["baseline_spec_sha256"]}])
    _put(wb, "Source_Artifacts", [{"file": k, "sha256": v} for k, v in c["source_file_sha256"].items()])
    _put(wb, "Version_Inventory", report["versions"])
    _put(wb, "Canonical_Baseline", [c])
    _put(wb, "Stage_Contract", report["stages"])
    for sheet, key in (
        ("MA", "MA_TREND"),
        ("BB", "BB_LOCATION"),
        ("RCI", "RCI_REVERSAL"),
        ("Volume", "VOLUME_PARTICIPATION"),
        ("Price_Action", "PRICE_ACTION_TRIGGER"),
        ("Board", "BOARD_SUPPORT_VETO"),
        ("ENTRY", "ENTRY"),
        ("EXIT", "EXIT"),
        ("Execution", "EXECUTION"),
    ):
        _put(wb, sheet, [by_id[key]])
    _put(wb, "Support_Resistance", [{"STRUCTURE_LAYER_PRESENT": False, "note": "No swing support or resistance stage. Prior-high break lives inside price action."}])
    _put(wb, "Timeframes", [{"SETUP_TIMEFRAME": c["setup_timeframe"], "TRIGGER_TIMEFRAME": c["trigger_timeframe"], "EXECUTION_TIMEFRAME": c["execution_timeframe"], "one_minute_role": c["one_minute_role"]}])
    _put(wb, "Portfolio_Reuse", report["portfolio_reuse"])
    _put(wb, "PB1_Status", [{"PB1_REQUIRED": False, "role": "READ_ONLY_STOCK_STATE_EVIDENCE", "V4_CHANGED": False, "V5_CREATED": False}])
    _put(wb, "Context_Status", [{"MARKET_CONTEXT_REQUIRED": False, "SECTOR_CONTEXT_REQUIRED": False}])
    _put(wb, "V2_Compatibility", [report["v2_compatibility"]])
    _put(wb, "Missing_Contracts", [{"contract": x} for x in report["missing_contracts"]])
    _put(wb, "Prospective_Firewall", [{"PROSPECTIVE_DATA_OPENED": False, "prospective_rows_read": 0, "exposed_through": "20260911", "classification": "DEVELOPMENT_EXPOSED"}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0, "new_pnl_run": False}])
    wb.save(OUT / "audit.xlsx")
