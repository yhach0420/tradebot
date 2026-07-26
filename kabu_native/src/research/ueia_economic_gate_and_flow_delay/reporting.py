"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.ueia_economic_gate_and_flow_delay.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
    return {
        "summary": _rows(p.get("verdict")),
        "source_run_reproduction": _rows(p.get("reproduction")),
        "candidate_selection_logic": _rows(p.get("selection_audit")),
        "all_12_candidates": _rows(p.get("all_12")),
        "split_local_vs_fixed": _rows(p.get("split_local_vs_fixed")),
        "fixed_threshold": _rows(p.get("fixed_threshold_table")),
        "cost_formula_audit": _rows(p.get("cost_formula")),
        "manual_path_checks": _rows(p.get("manual_checks")),
        "train_candidate_selection": _rows(p.get("train_selection")),
        "validation": _rows(p.get("validation")),
        "holdout": _rows(p.get("holdout")),
        "flow_timestamps": _rows(((p.get("delay") or {}).get("timestamps_sample"))),
        "delay_comparison": _rows(((p.get("delay") or {}).get("delay_summary"))),
        "edge_consumption": _rows(p.get("delay")),
        "daily": _rows(p.get("daily")),
        "symbols": _rows(p.get("symbols")),
        "duplicate_overlap": _rows(p.get("duplicate_overlap")),
        "execution_audit": _rows(p.get("execution_audit")),
        "integrity_audit": _rows(p.get("integrity")),
        "tests": _rows((p.get("tests") or {}).get("rows") or p.get("tests")),
    }


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    c = payload.get("completion") or {}
    md = [
        "# UEIA Economic Gate Repair + Flow Delay",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('40_final_verdict')}",
        f"- source: 20260725_202310",
        f"- selection: {c.get('5_why_b2_h5')}",
        f"- fixed candidate: {c.get('18_fixed_candidate')}",
        f"- VAL: {c.get('21_val_verdict')}",
        f"- HOLD: {c.get('24_hold_verdict')}",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
