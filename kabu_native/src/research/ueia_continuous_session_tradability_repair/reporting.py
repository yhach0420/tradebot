"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.ueia_continuous_session_tradability_repair.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
        "source_reproduction": _rows(p.get("reproduction")),
        "session_calendar": _rows(p.get("session_calendar")),
        "session_population": _rows(p.get("session_population")),
        "preopen_audit": _rows(p.get("preopen_audit")),
        "lunch_audit": _rows(p.get("lunch_audit")),
        "session_boundary_audit": _rows(p.get("session_boundary_audit")),
        "tradability_audit": _rows(p.get("tradability_audit")),
        "feature_lifecycle": _rows(p.get("feature_lifecycle")),
        "session_feature_drift": _rows(p.get("session_feature_drift")),
        "session_only_model": _rows(p.get("session_only_model")),
        "samples_original": _rows(p.get("samples_original")),
        "samples_continuous": _rows(p.get("samples_continuous")),
        "barrier_labels": _rows(p.get("barrier_labels")),
        "all_12_candidates": _rows(p.get("all_12")),
        "b4_h2": _rows(p.get("b4_h2")),
        "b4_h3": _rows(p.get("b4_h3")),
        "b4_h6": _rows(p.get("b4_h6")),
        "train_selection": _rows(p.get("train_selection")),
        "validation": _rows(p.get("validation")),
        "holdout": _rows(p.get("holdout")),
        "warmup_sensitivity": _rows(p.get("warmup_sensitivity")),
        "am_pm_comparison": _rows(p.get("am_pm_comparison")),
        "delay_train": _rows(((p.get("delay") or {}).get("train"))),
        "delay_validation": _rows(((p.get("delay") or {}).get("val"))),
        "daily": _rows(p.get("daily")),
        "symbols": _rows(p.get("symbols")),
        "execution_audit": _rows(p.get("execution_audit")),
        "integrity_audit": _rows(p.get("integrity")),
        "tests": _rows((p.get("tests") or {}).get("rows") or p.get("tests")),
    }


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    c = payload.get("completion") or {}
    md = [
        "# UEIA Continuous-Session Tradability Repair",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('58_final_verdict')}",
        f"- PREOPEN contamination: {c.get('48_PREOPEN_EDGE_CONTAMINATION')}",
        f"- S1 fixed candidate: {c.get('32_fixed_candidate')}",
        f"- VAL: {c.get('44_val_verdict')}",
        "",
        "Intraday continuous-session tradability only. Not a new ENTRY strategy.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
