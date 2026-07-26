"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.integrated_directional_entry_exit_strategy.constants import (
    REQUIRED_ARTIFACTS,
    REQUIRED_SHEETS,
)


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
        if isinstance(obj[0], dict):
            return obj[:5000]
        return [{"value": v} for v in obj[:5000]]
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            if isinstance(v, dict):
                flat = {"key": k}
                for sk, sv in v.items():
                    if isinstance(sv, (dict, list)):
                        flat[sk] = json.dumps(sv, ensure_ascii=False, default=str)
                    else:
                        flat[sk] = sv
                out.append(flat)
            else:
                out.append({"key": k, "value": v if not isinstance(v, (dict, list)) else json.dumps(v, default=str)})
        return out or [{"status": "empty"}]
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
        "strategy_specs": _rows(p.get("strategy_specs")),
        "all_20_strategies": _rows(p.get("all_20_rows")),
        "entry_comparison": _rows(p.get("entry_comparison")),
        "exit_comparison": _rows(p.get("exit_comparison")),
        "interaction_matrix": _rows(p.get("interaction_matrix")),
        "trades": _rows(p.get("trade_rows")),
        "train": _rows(p.get("train")),
        "validation": _rows(p.get("validation")),
        "holdout": _rows(p.get("holdout")),
        "daily": _rows(p.get("daily_rows")),
        "symbols": _rows(p.get("symbol_rows")),
        "exit_reasons": _rows(p.get("exit_reason_rows")),
        "holding_time": _rows(p.get("holding_time")),
        "mfe_mae": _rows(p.get("mfe_mae")),
        "cap5": _rows(p.get("cap5")),
        "pbv2_overlap": _rows(p.get("pbv2_overlap")),
        "execution_audit": _rows(p.get("execution_audit")),
        "integrity_audit": _rows(p.get("integrity")),
        "tests": _rows((p.get("tests") or {}).get("rows") or p.get("tests")),
    }


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    c = payload.get("completion") or {}
    md = [
        "# IDEES — Integrated Directional ENTRY-EXIT Strategy",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('39_final_verdict')}",
        f"- fixed strategy: {c.get('22_TRAIN_fixed_strategy')}",
        f"- ENTRY: {c.get('23_ENTRY_spec')}",
        f"- EXIT: {c.get('24_EXIT_spec')}",
        f"- VAL: {c.get('27_VAL_verdict')}",
        f"- HOLD: {c.get('28_HOLDOUT_run')}",
        f"- submit/cancel/live: {c.get('37_submit_cancel_live')}",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
