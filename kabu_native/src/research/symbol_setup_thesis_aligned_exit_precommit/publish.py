"""Publish the thesis-exit precommit."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_thesis_aligned_exit_precommit import ANALYSIS_ID
from research.symbol_setup_thesis_aligned_exit_precommit.contract import build
from research.symbol_setup_thesis_aligned_exit_precommit.isolation import OUT, write_overlap_n


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
    ex = report["exit"]
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        f"Exit {ex['EXIT_ID']}.",
        f"Contract SHA256: {ex['EXIT_CONTRACT_SHA256']}.",
        "One completed post-fill 1-minute bar that loses the moving-average thesis emits the exit.",
        "The signal bar is not reused. Persistence is 1 because that is the literal thesis, not because it was optimized.",
        "No economic result was calculated.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"analysis_id": ANALYSIS_ID, "verdict": report["verdict"], "next": report["next"]}])
    _put(wb, "Baseline", [{"id": report["baseline_id"], "sha256": report["baseline_spec_sha256"], "entry_changed": False}])
    _put(wb, "Thesis", [{"id": report["thesis_id"], "sha256": report["thesis_sha256"]}])
    _put(wb, "Thesis_Logic", [report["thesis_logic"]])
    _put(wb, "Prior_Wording_Erratum", [report["prior_wording"]])
    _put(wb, "Post_Fill_Observation", [{"first": ex["first_observation"], "signal_bar_reused": False, "k": 1, "timeframe": "1min_completed"}])
    _put(wb, "Invalidation_Reasons", [{"reason": k, "rule": v} for k, v in ex["reasons"].items()])
    _put(wb, "Exit_State_Machine", [{"state": ex["state"], "lost": ex["THESIS_LOST"]}])
    _put(wb, "Exit_Execution", [report["execution"]])
    _put(wb, "Event_Order", [{"step": i + 1, "event": name} for i, name in enumerate(ex["event_order"])])
    _put(wb, "Session_Close", [{"classification": "SESSION_FAIL_CLOSE", "is_thesis_loss": False, "sha256": report["execution"]["session_close_sha256"]}])
    _put(wb, "Entry_Execution", [report["entry_execution"]])
    _put(wb, "Portfolio", [report["portfolio"]])
    _put(wb, "Prior_Persistence_Evidence", [report["prior_wording"]])
    _put(wb, "Data_Surface", [report["surface"]])
    _put(wb, "No_Optimization", [{"k_tested": "none", "pnl": False, "pf": False}])
    _put(wb, "Prospective_Firewall", [{"opened": False}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0, "pb1_changed": False, "v4_changed": False, "v5_created": False}])
    wb.save(OUT / "audit.xlsx")
