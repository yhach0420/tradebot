"""Publish the diagnostic. Does not retune Alpha or PB1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility import ANALYSIS_ID
from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility.isolation import OUT, assert_write_root, write_overlap_n


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
    assert_write_root()
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    primary = report.get("actionability") or {}
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "This diagnostic does not create a strategy and does not open prospective data.",
        f"Failed complete-strategy funnel reproduced: {report.get('funnel_reproduced')}",
        f"3m observation n: {primary.get('observation_n')}",
        f"3m 8bps mean/median: {primary.get('adjusted_mean_bps')} / {primary.get('adjusted_median_bps')}",
        f"gates: {json.dumps(primary.get('gates'), sort_keys=True)}",
        "",
        report.get("narrative") or "",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"analysis_id": ANALYSIS_ID, "verdict": report["verdict"], "next": report["next"]}])
    _put(wb, "Funnel_Reproduction", [report.get("funnel") or {}])
    _put(wb, "Actionability_3m", [{k: v for k, v in primary.items() if k not in {"folds", "symbols", "gates"}}])
    _put(wb, "Folds", list((primary.get("folds") or {}).values()) if isinstance(primary.get("folds"), dict) else [])
    _put(wb, "Targets", primary.get("symbols") or [])
    _put(wb, "Gates", [primary.get("gates") or {}])
    _put(wb, "Sensitivity", report.get("sensitivity") or [])
    _put(wb, "PB1_Classification", report.get("pb1_classification") or [])
    _put(wb, "PB1_Events", report.get("pb1_events") or [])
    _put(wb, "Overlap", [report.get("overlap") or {}])
    _put(wb, "Base_Rate", report.get("base_rate_symbols") or [])
    _put(wb, "Direction", [report.get("direction") or {}])
    _put(wb, "Clocks", report.get("clocks") or [])
    _put(wb, "Decision", [report.get("diagnosis") or {}])
    _put(wb, "Prospective_Firewall", [{"PROSPECTIVE_DATA_OPENED": False, "prospective_rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0, "V4_CHANGED": False, "V5_CREATED": False}])
    wb.save(OUT / "audit.xlsx")
