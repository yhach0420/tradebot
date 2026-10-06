"""Publish the directional-volume mechanism research."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.symbol_setup_directional_volume_mechanism.isolation import OUT, write_overlap_n


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
    public = dict(report)
    public.pop("volume_rows", None)
    (OUT / "report.json").write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    analysis = report.get("analysis") or {}
    pre = {int(row["horizon_sec"]): row for row in analysis.get("pre_board") or []}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"VERDICT: {report.get('verdict')}",
        f"NEXT: {report.get('next')}",
        "",
        "ASK_CLASSIFIED_VOLUME is a same-snapshot at-ask proxy. It is not an exchange aggressor flag.",
        "BID_CLASSIFIED_VOLUME is the at-bid remainder.",
        "The only added gate is ask-classified volume strictly above bid-classified volume.",
        "",
        f"Repaired pre-board signals: {analysis.get('repaired_pre_board_n')}.",
        f"Interpretation: {analysis.get('interpretation')}.",
        "",
    ]
    for h in (30, 60, 180, 300):
        row = pre.get(h) or {}
        lines.append(
            f"{h}s raw-mid median {row.get('raw_mid_median')} | bid-anchor median {row.get('bid_anchor_median')} | ask-to-bid median {row.get('ask_to_bid_median')}"
        )
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    pre_rows = analysis.get("pre_board") or []
    by_h = {int(row["horizon_sec"]): [row] for row in pre_rows}
    lineages = analysis.get("lineages") or {}
    folds = analysis.get("folds") or {}
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": report.get("verdict"), "next": report.get("next"), "repair_id": report.get("repair_id"), "repair_spec_sha256": report.get("repair_spec_sha256"), "interpretation": analysis.get("interpretation"), "EXCHANGE_AGGRESSOR_FLAG": False}])
    _put(wb, "Identity", [report.get("identity") or {}])
    _put(wb, "Input_Parity", [analysis.get("parity") or {}])
    _put(wb, "Baseline_Volume", [analysis.get("baseline_clock") or {}])
    _put(wb, "Buy_Dominant", (analysis.get("buy_dominant") or []) + (analysis.get("split_delta") or []))
    _put(wb, "Not_Buy_Dominant", analysis.get("not_buy_dominant") or [])
    _put(wb, "Repaired_Signals", report.get("repaired_signals") or [])
    _put(wb, "Response_30", by_h.get(30) or [])
    _put(wb, "Response_60", by_h.get(60) or [])
    _put(wb, "Response_180", by_h.get(180) or [])
    _put(wb, "Response_300", by_h.get(300) or [])
    _put(wb, "Baseline_vs_Repair", analysis.get("comparison") or [])
    _put(wb, "Flow_Classification_Quality", (analysis.get("classification_quality") or []) + [analysis.get("locked_or_crossed") or {}])
    _put(wb, "Original18", [lineages.get("ORIGINAL18") or {}])
    _put(wb, "Extension17", [lineages.get("EXTENSION17") or {}])
    _put(wb, "Folds", [folds.get(name) or {"group": name} for name in ("FOLD_A", "FOLD_B", "FOLD_C")])
    _put(wb, "Board_Diagnostic", (analysis.get("board_pass") or []) + (analysis.get("board_veto") or []))
    _put(wb, "Concentration", (analysis.get("concentration_rows") or []) + [analysis.get("concentration_day") or {}, analysis.get("concentration_symbol") or {}])
    _put(wb, "Gates", [{"gate": key, "pass": value} for key, value in (analysis.get("gates") or {}).items()])
    _put(wb, "No_PnL", [report.get("no_pnl") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0, "EXCHANGE_AGGRESSOR_FLAG": False}])
    wb.save(OUT / "audit.xlsx")
