"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.integrated_initial_impulse_continuation.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
    return {
        "summary": _rows(p.get("verdict")),
        "episodes": _rows(p.get("episode_stats")),
        "state_transitions": _rows(p.get("state_counts")),
        "entries": _rows(p.get("entry_sample")),
        "exits": _rows((p.get("train_arms") or {}).get("A5", {}).get("reasons")),
        "arms": _rows(p.get("train_arms")),
        "incremental": _rows(p.get("incremental")),
        "daily": _rows(((p.get("train_arms") or {}).get("A5") or {}).get("by_day")),
        "symbols": _rows(p.get("symbol_dependency")),
        "execution_audit": _rows(p.get("execution_audit")),
        "integrity_audit": _rows(p.get("integrity")),
        "tests": _rows((p.get("tests") or {}).get("rows") or p.get("tests")),
    }


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    c = payload.get("completion") or {}
    md = [
        "# Integrated Initial Impulse Continuation (IIC)",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('29_final_verdict')}",
        f"- TRAIN episodes/entries: {c.get('4_all_episodes')} / {c.get('5_entry_n')}",
        f"- A5 PF/PnL: {c.get('8_pf')} / {c.get('9_pnl')}",
        f"- VALIDATION: {c.get('22_validation')}",
        f"- CAP5: {c.get('23_cap5')}",
        "",
        "Scenario-integrated strategy. ENTRY and EXIT are one contract.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
