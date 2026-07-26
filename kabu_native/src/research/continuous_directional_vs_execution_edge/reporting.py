"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.continuous_directional_vs_execution_edge.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


def _cell(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool, str)):
        return v
    if isinstance(v, datetime):
        return v.isoformat()
    return json.dumps(v, ensure_ascii=False, default=str)


def _rows(obj: Any) -> list[dict[str, Any]]:
    if obj is None:
        return [{"status": "empty"}]
    if isinstance(obj, list):
        if not obj:
            return [{"status": "empty"}]
        return obj[:500] if isinstance(obj[0], dict) else [{"value": v} for v in obj[:500]]
    if isinstance(obj, dict):
        return [
            {"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v}
            for k, v in obj.items()
        ]
    return [{"value": str(obj)}]


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name in REQUIRED_SHEETS:
        rows = list(sheets.get(name) or [{"status": "empty"}])
        ws = wb.create_sheet(str(name)[:31])
        keys: list[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        if not keys:
            ws.append(["empty"])
            continue
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def build_sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {name: _rows(p.get(name) if name != "summary" else p.get("verdict")) for name in REQUIRED_SHEETS}


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    # map sheet names to payload keys
    sheet_map = {
        "summary": payload.get("verdict"),
        "source_reproduction": payload.get("reproduction"),
        "quote_integrity": payload.get("quote_integrity"),
        "spread_distribution": payload.get("spread_distribution"),
        "mechanical_down": payload.get("mechanical_down"),
        "mechanical_down_examples": payload.get("mechanical_down_examples"),
        "directional_labels": payload.get("directional_labels"),
        "execution_labels": payload.get("execution_labels"),
        "mid_vs_bid_vs_ask": payload.get("mid_vs_bid_vs_ask"),
        "spread_cohorts": payload.get("spread_cohorts"),
        "feature_groups": payload.get("feature_groups"),
        "all_candidates": payload.get("all_candidates"),
        "train_selection": payload.get("train_selection"),
        "validation_direction": payload.get("validation_direction"),
        "validation_execution": payload.get("validation_execution"),
        "am_pm": payload.get("am_pm"),
        "daily": payload.get("daily"),
        "symbols": payload.get("symbols"),
        "holdout": payload.get("holdout"),
        "execution_audit": payload.get("execution_audit"),
        "integrity_audit": payload.get("integrity"),
        "tests": (payload.get("tests") or {}).get("rows") or payload.get("tests"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    c = payload.get("completion") or {}
    md = [
        "# CDEED — Continuous Directional vs Execution Edge",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('56_final_verdict')}",
        f"- Gate A: {c.get('31_gate_a')}",
        f"- Gate B: {c.get('36_gate_b')}",
        f"- spread contamination: {c.get('45_SPREAD_BARRIER_LABEL_CONTAMINATION')}",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", {k: _rows(v) for k, v in sheet_map.items()})
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
