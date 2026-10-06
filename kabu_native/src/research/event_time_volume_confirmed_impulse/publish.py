"""Publish the event-time impulse result. No raw event dump."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.event_time_volume_confirmed_impulse.isolation import OUT, write_overlap_n


def _put(wb: Workbook, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        ws.append(["none"])
        return
    keys: list[str] = []
    flat = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            item[str(key)] = value
            if str(key) not in keys:
                keys.append(str(key))
        flat.append(item)
    ws.append(keys)
    for item in flat:
        ws.append([item.get(key) for key in keys])


def _metric_rows(population: str, block: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for horizon, metrics in block.items():
        for metric, stat in metrics.items():
            rows.append({"population": population, "horizon_sec": horizon, "metric": metric, **stat})
    return rows


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    decision = report.get("decision") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "This is an event-time impulse test on the already exposed capture.",
        "It does not repair the 1-minute setup and it does not search a volume multiplier.",
        "Ask-classified volume is a same-event price versus ask proxy. No exchange aggressor flag exists.",
        "",
        f"FULL n: {(decision.get('n') or {}).get('FULL')}.",
        f"PRICE_BREAK_ONLY n: {(decision.get('n') or {}).get('PRICE_BREAK_ONLY')}.",
        f"VOLUME_BREAK_NO_BUY n: {(decision.get('n') or {}).get('VOLUME_BREAK_NO_BUY')}.",
        f"Capture dates read: {report.get('min_capture_date_read')} through {report.get('max_capture_date_read')}.",
    ]
    if report.get("information"):
        lines.extend(["", str(report["information"])])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    horizons = decision.get("horizons") or {}
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {
        "Manifest": [report.get("manifest") or {}],
        "Data_Seal": [report.get("data_seal") or {}],
        "Event_Semantics": report.get("event_semantics") or [],
        "Coverage": [report.get("coverage") or {}],
        "Signal_Episodes": [report.get("episodes") or {}],
        "Full_Mechanism": _metric_rows("FULL", horizons.get("FULL") or {}),
        "PriceBreak_Only": _metric_rows("PRICE_BREAK_ONLY", horizons.get("PRICE_BREAK_ONLY") or {}),
        "VolumeBreak_NoBuy": _metric_rows("VOLUME_BREAK_NO_BUY", horizons.get("VOLUME_BREAK_NO_BUY") or {}),
        "Horizons": (
            _metric_rows("FULL", horizons.get("FULL") or {})
            + _metric_rows("PRICE_BREAK_ONLY", horizons.get("PRICE_BREAK_ONLY") or {})
            + _metric_rows("VOLUME_BREAK_NO_BUY", horizons.get("VOLUME_BREAK_NO_BUY") or {})
        ),
        "Original18": [decision.get("original18") or {}],
        "Extension17": [decision.get("extension17") or {}],
        "Folds": list(decision.get("folds") or []),
        "Concentration": [report.get("concentration") or {}],
        "Verdict": [report.get("verdict_row") or {}],
        "Safety": [report.get("safety") or {}],
    }
    for name, rows in sheets.items():
        _put(wb, name, rows)
    wb.save(OUT / "audit.xlsx")
