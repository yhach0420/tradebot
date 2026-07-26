"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.idees_fixed_candidate_concentration_oos.constants import (
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
                    flat[sk] = sv if not isinstance(sv, (dict, list)) else json.dumps(sv, ensure_ascii=False, default=str)
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
        "candidate_spec": _rows(p.get("candidate_spec")),
        "reproduction": _rows(p.get("reproduction")),
        "train_full": _rows(p.get("train_full")),
        "symbol_concentration": _rows(p.get("symbol_rows")),
        "yen_bps_concentration": _rows(p.get("yen_bps")),
        "exclude_top1": _rows(p.get("r1")),
        "exclude_top3": _rows(p.get("r2")),
        "leave_one_symbol_out": _rows((p.get("r3") or {}).get("rows") or p.get("r3")),
        "one_trade_per_symbol_session": _rows(p.get("r4")),
        "top_trade_removed": _rows(p.get("r5")),
        "daily": _rows(p.get("daily_rows")),
        "time_bands": _rows(p.get("time_band_rows")),
        "validation": _rows(p.get("validation")),
        "holdout": _rows(p.get("holdout")),
        "x1_x5_comparison": _rows(p.get("x1_x5")),
        "exit_reasons": _rows(p.get("exit_reason_rows")),
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
    spec = payload.get("candidate_spec") or {}
    md = [
        "# IDEES-CC — Fixed Candidate Concentration and OOS Closure",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('43_final_verdict')}",
        f"- candidate: E1_X5",
        f"- TRAIN resilience: {c.get('18_TRAIN_resilience')}",
        f"- VAL: {c.get('26_VAL_verdict')}",
        f"- HOLD: {c.get('33_HOLD_verdict')}",
        f"- submit/cancel/live: {c.get('41_submit_cancel_live')}",
        "",
        "## Fixed ENTRY",
        str(spec.get("entry")),
        "",
        "## Fixed EXIT",
        str(spec.get("exit")),
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
