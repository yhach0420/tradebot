"""Publish the precommit artifacts."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_baseline_minimal_completion import ANALYSIS_ID
from research.symbol_setup_baseline_minimal_completion.contract import build
from research.symbol_setup_baseline_minimal_completion.isolation import OUT, write_overlap_n


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


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    c = report["coverage"]
    th = report["thesis"]
    fam = report["mechanism_family"]
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        f"Baseline {report['baseline_id']} SHA {report['baseline_spec_sha256']}.",
        "ENTRY was not changed.",
        f"Thesis {th['THESIS_ID']} SHA {th['THESIS_SHA256']}.",
        "No prior exit is an exact frozen thesis invalidation.",
        f"EMA persistence is mechanism discovery only. Family SHA {fam['SYMBOL_SETUP_EXIT_MECHANISM_FAMILY_SHA256']}.",
        f"Exact capture sessions with an AM window: {c['eligible_session_n']}.",
        "No economic result was calculated.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"analysis_id": ANALYSIS_ID, "verdict": report["verdict"], "next": report["next"]}])
    _put(wb, "Baseline_Identity", [{"baseline_id": report["baseline_id"], "sha256": report["baseline_spec_sha256"], "entry_changed": False}])
    _put(wb, "Entry_Thesis", [th])
    _put(wb, "Stage_Roles", report["stage_roles"])
    _put(wb, "Prior_Exit_Research", report["prior_exits"])
    _put(wb, "Exit_Mechanisms", [fam])
    _put(wb, "Exit_Contract", [{"exit_id": None, "exact": False, "thesis_aligned": False, "source": report["exit_source"]}])
    _put(wb, "Execution", [report["execution"]])
    _put(wb, "Portfolio", [report["portfolio"]])
    _put(wb, "Data_Requirements", [{"need": "sealed push capture with price, cumulative volume, bid, ask, quantities, and the post-fill quote path"}])
    _put(wb, "Data_Coverage", [c])
    _put(wb, "Timeframe_Status", [{"setup": "1min_completed", "trigger": "1min_completed", "changed": False, "deficiency": "ARCHITECTURAL_DEFICIENCY_CANDIDATE"}])
    _put(wb, "Structure_Status", [{"STRUCTURE_LAYER_PRESENT": False, "support_resistance_added": False}])
    _put(wb, "Context_Status", [{"PB1_REQUIRED": False, "MARKET_CONTEXT_REQUIRED": False, "SECTOR_CONTEXT_REQUIRED": False}])
    _put(wb, "Complete_Strategy_Identity", [{"id": None, "sha256": None, "reason": "exact thesis exit is not frozen"}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "cutoff": "20260911", "prospective_from": "20260924"}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0, "new_pnl_run": False}])
    wb.save(OUT / "audit.xlsx")
