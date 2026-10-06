"""Publish the interaction result. No raw event dump."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.context_conditioned_stock_setup.isolation import OUT, write_overlap_n


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


def _horizon_rows(label: str, block: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for horizon, stat in block.items():
        rows.append({"population": label, "horizon": horizon, **stat})
    return rows


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "The stock trigger is unchanged. Only market and sector participation are added as context.",
        "Context excludes the target symbol. Breadth must be strictly above one half.",
        "",
        f"Aligned events: {report.get('aligned_n')}.",
        f"Not-aligned events: {report.get('not_aligned_n')}.",
        f"Interaction delta at 5 minutes, bps: {report.get('interaction_delta')}.",
        f"Max historical date read: {report.get('max_date')}.",
    ]
    if report.get("information"):
        lines.extend(["", str(report["information"])])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    decision = report.get("decision") or {}
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Manifest": [report.get("manifest") or {}],
        "Data_Seal": [report.get("data_seal") or {}],
        "Universe": [report.get("universe") or {}],
        "Context_Contract": report.get("context_contract") or [],
        "Stock_Setup": report.get("stock_setup") or [],
        "Population": [report.get("population") or {}],
        "Aligned": _horizon_rows("ALIGNED", (decision.get("horizons") or {}).get("aligned") or {}),
        "Not_Aligned": _horizon_rows("NOT_ALIGNED", (decision.get("horizons") or {}).get("not_aligned") or {}),
        "Interaction": [report.get("interaction") or {}],
        "DEV": [decision.get("dev") or {}],
        "C1": [decision.get("c1") or {}],
        "Folds": list(decision.get("dev_folds") or []) + list(decision.get("c1_folds") or []),
        "Relative_Return": [decision.get("relative") or {}],
        "Concentration": [report.get("concentration") or {}],
        "Capture_Confirmation": [report.get("capture") or {}],
        "Actionability": [report.get("actionability") or {}],
        "Verdict": [report.get("verdict_row") or {}],
        "Safety": [report.get("safety") or {}],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
