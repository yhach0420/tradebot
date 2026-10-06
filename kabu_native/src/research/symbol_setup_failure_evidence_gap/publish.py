"""Publish the evidence-gap resolution. Does not touch the frozen failure files."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from research.am_c0_indicator_exit.isolation import FORBIDDEN_WRITE_PREFIXES, TODAY
from research.symbol_setup_failure_evidence_gap import CANONICAL_STAGE_ORDER

_ = TODAY
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "symbol_setup_failure_evidence_gap_v1"
PRIOR = (
    NATIVE / "results" / "research" / "symbol_setup_baseline_failure_decomposition_v1",
    NATIVE / "results" / "research" / "symbol_setup_baseline_complete_strategy_development_v1",
    NATIVE / "src" / "research" / "simple_tech_entry_family",
    NATIVE / "data" / "market_capture",
)


def write_overlap_n() -> int:
    n = 0
    ws = str(OUT.resolve())
    forbidden = [p.resolve() for p in PRIOR if p.exists()]
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if pref.exists():
            forbidden.append(pref.resolve())
    for item in forbidden:
        fs = str(item)
        if ws == fs or ws.startswith(fs + os.sep) or fs.startswith(ws + os.sep):
            n += 1
    return n


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


def _clock_rows(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in signals:
        rows.append(
            {
                "date": row.get("date"),
                "symbol": row.get("symbol"),
                "lineage": row.get("lineage"),
                "fold": row.get("fold"),
                "board_pass": row.get("board_pass"),
                "signal_bar_finalize_t": row.get("signal_bar_finalize_t"),
                "signal_close": row.get("signal_close"),
                "q0_t": row.get("q0_t"),
                "q0_delay_ms": row.get("q0_delay_ms"),
                "bid0": row.get("bid0"),
                "ask0": row.get("ask0"),
                "mid0": row.get("mid0"),
                "spread0": row.get("spread0"),
                "last0": row.get("last0"),
            }
        )
    return rows


def _quote_rows(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in signals:
        for horizon, cell in (row.get("horizons") or {}).items():
            rows.append(
                {
                    "date": row.get("date"),
                    "symbol": row.get("symbol"),
                    "horizon_sec": horizon,
                    "target_nominal_t": cell.get("target_nominal_t"),
                    "qh_t": cell.get("qh_t"),
                    "qh_minus_target_sec": cell.get("qh_minus_target_sec"),
                    "bidh": cell.get("bidh"),
                    "askh": cell.get("askh"),
                    "midh": cell.get("midh"),
                    "lasth": cell.get("lasth"),
                }
            )
    return rows


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    analysis = report.get("analysis") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        f"PRICE_REFERENCE_CLASSIFICATION: {analysis.get('price_reference_classification')}",
        f"PRIMARY_DEFICIENCY: {report.get('primary_deficiency')}",
        "",
        "The frozen complete strategy was not modified.",
        "The historical not-identified verdict was not rewritten.",
        f"Canonical stage order: {' -> '.join(CANONICAL_STAGE_ORDER)}",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    signals = list(report.get("signals") or [])
    tables = analysis.get("tables") or {}
    bars = analysis.get("bars") or {}
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": report.get("verdict"), "next": report.get("next"), "primary_deficiency": report.get("primary_deficiency"), "strategy_sha256": report.get("complete_strategy_sha256")}])
    _put(wb, "Frozen_Failure", [report.get("frozen_failure") or {}])
    _put(wb, "Stage_Order_Audit", [report.get("stage_order_audit") or {}])
    _put(wb, "Stage_Contribution_Corrected", analysis.get("stages") or [])
    _put(wb, "Signal_Clock", _clock_rows(signals))
    _put(wb, "Quote_Clock", _quote_rows(signals))
    price_rows = []
    for name, table in tables.items():
        price_rows.extend(table)
    _put(wb, "Price_Decomposition", price_rows)
    bar_rows = []
    for name, table in bars.items():
        bar_rows.extend(table)
    _put(wb, "Bar_Response", bar_rows)
    _put(wb, "Trade_Price_Response", [row for row in price_rows if row.get("population") == "ALL_TECHNICAL_SIGNALS"])
    timing = analysis.get("timing") or {}
    _put(wb, "Timing_Residual", [{k: v} if not isinstance(v, dict) else {"metric": k, **v} for k, v in timing.items()])
    _put(wb, "Board_Pass_Veto", (tables.get("BOARD_PASS") or []) + (tables.get("BOARD_VETO") or []))
    _put(wb, "Lineage", (tables.get("ORIGINAL18") or []) + (tables.get("EXTENSION17") or []))
    _put(wb, "Folds", (tables.get("FOLD_A") or []) + (tables.get("FOLD_B") or []) + (tables.get("FOLD_C") or []))
    _put(wb, "Classification", [{"overall": analysis.get("price_reference_classification"), "subgroup": analysis.get("subgroup_classification"), "order_effect": analysis.get("order_effect")}])
    _put(wb, "No_Repair", [report.get("no_repair") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
