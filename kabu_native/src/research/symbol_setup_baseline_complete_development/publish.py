"""Publish the development run. Writes only the development directory."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from research.am_c0_indicator_exit.isolation import FORBIDDEN_WRITE_PREFIXES, TODAY

_ = TODAY
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "symbol_setup_baseline_complete_strategy_development_v1"
PRIOR = (
    NATIVE / "results" / "research" / "symbol_setup_baseline_complete_strategy_precommit_v1",
    NATIVE / "src" / "research" / "symbol_setup_baseline_complete_strategy_precommit",
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


def publish(report: dict[str, Any]) -> None:
    if write_overlap_n() != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    body = dict(report)
    trades = body.pop("trades", [])
    (OUT / "report.json").write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    verdict = body.get("verdict")
    lines = [
        f"# {body.get('analysis_id')}",
        "",
        f"VERDICT: {verdict}",
        f"NEXT: {body.get('next')}",
        "",
        "The frozen complete strategy was replayed on the 35-session surface.",
        "No rule was changed after the result.",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    _put(wb, "Manifest", [{"verdict": verdict, "next": body.get("next"), "strategy_sha256": body.get("complete_strategy_sha256")}])
    _put(wb, "Identity", [body.get("identity") or {}])
    _put(wb, "Funnel", [body.get("funnel") or {}])
    _put(wb, "Trades", trades)
    _put(wb, "Daily", body.get("daily") or [])
    _put(wb, "Symbols", body.get("symbols") or [])
    _put(wb, "Exit_Reasons", body.get("reasons") or [])
    _put(wb, "Reentry", [body.get("reentry") or {}])
    _put(wb, "Original18", [((body.get("groups") or {}).get("ORIGINAL18") or {})])
    _put(wb, "Extension17", [((body.get("groups") or {}).get("EXTENSION17") or {})])
    _put(wb, "Folds", [{"fold": k, **v} for k, v in (body.get("groups") or {}).items() if str(k).startswith("FOLD_")])
    _put(wb, "Concentration", [body.get("concentration") or {}])
    _put(wb, "Gates", [body.get("gates") or {}])
    _put(wb, "Failure_Evidence", [body.get("failure_evidence") or {}])
    _put(wb, "Determinism", [body.get("determinism") or {}])
    _put(wb, "Prospective_Firewall", [{"opened": False, "rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0}])
    wb.save(OUT / "audit.xlsx")
