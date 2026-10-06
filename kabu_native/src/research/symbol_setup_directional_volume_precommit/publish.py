"""Publish the directional-volume precommit."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_directional_volume_precommit.isolation import OUT, write_overlap_n


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
    analysis = report.get("analysis") or {}
    cov = analysis.get("coverage") or {}
    recon = analysis.get("reconciliation") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "Signed volume is the completed-bar sum of TradingVolume deltas classified by the same event's price versus its bid and ask.",
        "Ask volume is a print at or above that event's ask. Bid volume is a print at or below that event's bid that was not already counted as ask volume.",
        "Uptick and downtick volume stay audit-only.",
        "",
        f"Eligible bars: {cov.get('eligible_bar_n')}.",
        f"Both signed fields finite: {cov.get('both_available_n')} ({cov.get('coverage_fraction')}).",
        f"Classified volume fraction: {recon.get('classification_fraction')}.",
        f"Repair ready: {report.get('repair_ready')}.",
        f"Repair SHA256: {report.get('repair_spec_sha256')}.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sem = report.get("semantics") or {}
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": report.get("verdict"), "next": report.get("next"), "repair_id": report.get("repair_id"), "repair_spec_sha256": report.get("repair_spec_sha256"), "repair_ready": report.get("repair_ready")}])
    _put(wb, "Parent_Identity", [report.get("parent") or {}])
    _put(wb, "Prior_Repair_Closure", [report.get("prior_repair") or {}])
    _put(wb, "Signed_Volume_Semantics", [sem.get("ask_vol") or {}, sem.get("bid_vol") or {}, sem.get("up_vol") or {}, sem.get("down_vol") or {}])
    _put(wb, "Volume_Reconciliation", [recon])
    _put(wb, "Coverage", [cov] + (analysis.get("by_group") or []) + (analysis.get("by_date") or []))
    _put(wb, "Baseline_Parity", [analysis.get("parity") or {}])
    _put(wb, "Repair_Contract", [report.get("repair_spec") or {"defined": False}])
    _put(wb, "Research_Gates", [report.get("research_gates") or {}])
    _put(wb, "No_Optimization", [report.get("no_optimization") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
