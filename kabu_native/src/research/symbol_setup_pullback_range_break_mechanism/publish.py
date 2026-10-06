"""Publish the range-break mechanism research."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_pullback_range_break_mechanism.isolation import OUT, write_overlap_n


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


def _md(report: dict[str, Any]) -> str:
    analysis = report.get("analysis") or {}
    pre = {int(row["horizon_sec"]): row for row in analysis.get("pre_board") or []}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        f"Repair {report.get('repair_id')}.",
        f"Spec SHA256: {report.get('repair_spec_sha256')}.",
        "",
        "The only change is the price-action conjunct: close above the prior three completed highs.",
        "No complete-strategy PnL was calculated.",
        "",
        f"Volume-pass input: {(analysis.get('parity') or {}).get('volume_pass_n')}.",
        f"Baseline signals: {(analysis.get('parity') or {}).get('baseline_signal_n')}.",
        f"Repaired pre-board signals: {analysis.get('repaired_pre_board_n')}.",
        f"Repaired board pass: {analysis.get('repaired_board_pass_n')}.",
        "",
    ]
    for h in (30, 60, 180, 300):
        row = pre.get(h) or {}
        lines.append(
            f"{h}s raw-mid median {row.get('raw_mid_median')} | "
            f"bid-anchor median {row.get('bid_anchor_median')} | "
            f"ask-to-bid median {row.get('ask_to_bid_median')}"
        )
    lines.append("")
    lines.append(f"Mechanism gates pass: {analysis.get('mechanism_pass')}.")
    return "\n".join(lines) + "\n"


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (OUT / "report.md").write_text(_md(report), encoding="utf-8")
    analysis = report.get("analysis") or {}
    pre = analysis.get("pre_board") or []
    by_h = {int(row["horizon_sec"]): [row] for row in pre}
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": report.get("verdict"), "next": report.get("next"), "repair_id": report.get("repair_id"), "repair_spec_sha256": report.get("repair_spec_sha256")}])
    _put(wb, "Identity", [report.get("identity") or {}])
    _put(wb, "Input_Parity", [analysis.get("parity") or {}])
    _put(wb, "Baseline", [analysis.get("baseline_clock") or {}])
    _put(wb, "Repaired_Signals", report.get("repaired_signals") or [])
    _put(wb, "Response_30", by_h.get(30) or [])
    _put(wb, "Response_60", by_h.get(60) or [])
    _put(wb, "Response_180", by_h.get(180) or [])
    _put(wb, "Response_300", by_h.get(300) or [])
    _put(wb, "Baseline_vs_Repair", analysis.get("comparison") or [])
    lineages = analysis.get("lineages") or {}
    _put(wb, "Original18", [lineages.get("ORIGINAL18") or {}])
    _put(wb, "Extension17", [lineages.get("EXTENSION17") or {}])
    folds = analysis.get("folds") or {}
    _put(wb, "Folds", [folds.get(name) or {"group": name} for name in ("FOLD_A", "FOLD_B", "FOLD_C")])
    _put(wb, "Board_Diagnostic", (analysis.get("board_pass") or []) + (analysis.get("board_veto") or []))
    _put(wb, "Concentration", (analysis.get("concentration_rows") or []) + [analysis.get("concentration_day") or {}, analysis.get("concentration_symbol") or {}])
    gates = analysis.get("gates") or {}
    _put(wb, "Gates", [{"gate": key, "pass": value} for key, value in gates.items()])
    _put(wb, "No_PnL", [report.get("no_pnl") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
