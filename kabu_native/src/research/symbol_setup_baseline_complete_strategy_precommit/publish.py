"""Publish the complete-strategy precommit."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_baseline_complete_strategy_precommit import (
    ANALYSIS_ID,
    BASELINE_ID,
    BASELINE_SPEC_SHA256,
    ENTRY_EXECUTION_ID,
    ENTRY_EXECUTION_SHA256,
    EXIT_CONTRACT_SHA256,
    EXIT_EXECUTION_ID,
    EXIT_EXECUTION_SHA256,
    EXIT_ID,
    PORTFOLIO_ID,
    PORTFOLIO_SHA256,
    THESIS_ID,
    THESIS_SHA256,
)
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18, build
from research.symbol_setup_baseline_complete_strategy_precommit.isolation import OUT, write_overlap_n
from research.symbol_setup_baseline_complete_strategy_precommit.runner import EVENT_ORDER


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
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "The complete strategy is the frozen V1 symbol setup, the literal moving-average thesis exit, and V1 portfolio accounting.",
        "No economic result was calculated.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    folds = report["folds"]
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"analysis_id": ANALYSIS_ID, "verdict": report["verdict"], "next": report["next"], "complete_strategy_sha256": report["complete_strategy_sha256"]}])
    _put(wb, "Component_Identity", [report["strategy"] or {"status": report["verdict"]}])
    _put(wb, "Universe", report["universe_sessions"])
    _put(wb, "Position_Size", [report["position_size"]])
    _put(wb, "Cost_Accounting", [report["cost"]])
    _put(wb, "Entry", [{"changed": False, "chain": "MA_TREND, BB_LOCATION, RCI_REVERSAL, VOLUME_PARTICIPATION, PRICE_ACTION_TRIGGER, BOARD_SUPPORT_VETO"}])
    _put(wb, "Entry_Execution", [{"id": ENTRY_EXECUTION_ID, "sha256": ENTRY_EXECUTION_SHA256}])
    _put(wb, "Thesis", [{"id": THESIS_ID, "sha256": THESIS_SHA256}])
    _put(wb, "Exit", [{"id": EXIT_ID, "sha256": EXIT_CONTRACT_SHA256, "k": 1, "changed": False}])
    _put(wb, "Exit_Execution", [{"id": EXIT_EXECUTION_ID, "sha256": EXIT_EXECUTION_SHA256}])
    _put(wb, "Portfolio", [{"id": PORTFOLIO_ID, "sha256": PORTFOLIO_SHA256, "cap": 5, "pyramiding": False}])
    _put(wb, "Event_Order", [{"step": i + 1, "event": name} for i, name in enumerate(EVENT_ORDER)])
    _put(wb, "Invalid_Data", [{"event": "INVALID_THESIS_OBSERVATION", "action": "FAIL_CLOSE_INVALID_DATA", "counts_as_thesis_loss": False}])
    _put(wb, "Session_Close", [{"event": "SESSION_FAIL_CLOSE", "counts_as_thesis_loss": False}])
    _put(wb, "Data_Surface", [{"n": report["surface_n"], "classification": "DEVELOPMENT_EXPOSED", "baseline_id": BASELINE_ID, "baseline_sha256": BASELINE_SPEC_SHA256}])
    _put(wb, "Original18", [{"date": d, "role": "DEVELOPMENT_LINEAGE_DIAGNOSTIC"} for d in ORIGINAL18])
    _put(wb, "Extension17", [{"date": d, "role": "EXPOSED_EXTENSION_DIAGNOSTIC"} for d in EXTENSION17])
    _put(wb, "Chronological_Folds", [{"fold": k, "n": len(v), "first": v[0], "last": v[-1]} for k, v in folds.items()])
    _put(wb, "Economic_Metrics", [{"name": n, "value": None} for n in ("signal_n", "pending_n", "expired_n", "fill_n", "fill_rate", "trade_n", "net_pnl_yen", "PF", "max_drawdown_yen")])
    _put(wb, "Economic_Gates", [{"gate": g, "frozen": True, "evaluated": False} for g in ("G1", "G2", "G3", "G4", "G5", "G6", "G7")])
    _put(wb, "Concentration", [{"report": "daily, symbol, top1 trade, top5 trades, top1 day, top1 symbol", "evaluated": False}])
    _put(wb, "Context_Firewall", [{"pb1": False, "market": False, "sector": False, "m3": False, "futures": False}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
