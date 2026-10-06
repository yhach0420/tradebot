"""Publish the repair precommit."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_one_mechanism_repair_precommit.isolation import OUT, write_overlap_n


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
                v = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
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
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    spec = report.get("repair_spec") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        f"Repair {report.get('repair_id')}.",
        f"Spec SHA256: {report.get('repair_spec_sha256')}.",
        "",
        "Only the one-bar high break inside Price Action is replaced.",
        "The replacement is a close above the maximum of the prior three completed highs.",
        "No portfolio result was calculated.",
        "",
        str(spec.get("REPAIRED_PRICE_ACTION") or ""),
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": report.get("verdict"), "next": report.get("next"), "repair_id": report.get("repair_id"), "repair_spec_sha256": report.get("repair_spec_sha256")}])
    _put(wb, "Frozen_Failure", [report.get("frozen_failure") or {}])
    _put(wb, "Repair_Rule", [spec])
    _put(wb, "Local_Resistance", [spec.get("LOCAL_RESISTANCE") or {}])
    _put(wb, "Unchanged_Components", [spec.get("unchanged") or {}])
    _put(wb, "Population", [report.get("population") or {}])
    _put(wb, "Baseline_180", [report.get("baseline_180") or {}])
    _put(wb, "Research_Contract", [spec.get("research_contract") or {}])
    _put(wb, "Gates", [report.get("gates") or {}])
    _put(wb, "No_Run", [report.get("no_run") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
