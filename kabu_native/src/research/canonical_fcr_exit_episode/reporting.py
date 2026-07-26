"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_fcr_exit_episode.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
        return obj[:400] if isinstance(obj[0], dict) else [{"value": v} for v in obj[:400]]
    if isinstance(obj, dict):
        return [{"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v} for k, v in obj.items()]
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
    tr = p.get("train_arms") or {}
    classes = p.get("class_counts") or {}
    return {
        "README": [{"phase": "canonical_fcr_exit_episode", "run_id": p.get("run_id")}],
        "SOURCE_AUDIT": _rows(p.get("source_audit")),
        "FROZEN_ENTRY": _rows(p.get("frozen_entry")),
        "POST_ENTRY_STATES": _rows(p.get("state_note")),
        "HEALTHY_ADVANCE": _rows({"n": classes.get("HEALTHY_ADVANCE")}),
        "TEMPORARY_NOISE": _rows({"n": classes.get("TEMPORARY_NOISE")}),
        "FALSE_RECLAIM": _rows({"n": classes.get("FALSE_RECLAIM")}),
        "NO_PROGRESS": _rows({"n": classes.get("NO_PROGRESS")}),
        "WINNER_GIVEBACK": _rows({"n": classes.get("WINNER_GIVEBACK")}),
        "X0": _rows(tr.get("X0")),
        "X1": _rows(tr.get("X1")),
        "X2": _rows(tr.get("X2")),
        "X3": _rows(tr.get("X3")),
        "X4": _rows(tr.get("X4")),
        "X5": _rows(tr.get("X5")),
        "INCREMENTAL_EXIT": _rows(p.get("incremental")),
        "TRAIN_RESULTS": _rows(tr),
        "VALIDATION_RESULTS": _rows(p.get("val_arms")),
        "CAP5": _rows(p.get("cap5")),
        "STRATEGY_EVAL": _rows(p.get("strategy")),
        "TESTS": _rows((p.get("tests") or {}).get("rows") or p.get("tests")),
        "VERDICT": _rows(p.get("verdict")),
    }


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    v = payload.get("verdict") or {}
    c = payload.get("completion") or {}
    md = [
        "# Canonical FCR EXIT Episode Construction",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {v.get('final_verdict')}",
        f"- EXIT direction: A hold / B hold / C cut / D cut / E take profit",
        f"- TRAIN X5 PF/PnL: {c.get('16_final_pf')} / {c.get('17_final_pnl')}",
        f"- CAP5: {c.get('18_cap5')}",
        f"- VALIDATION: {c.get('19_validation')}",
        "",
        "ENTRY frozen. EXIT episodes only.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
