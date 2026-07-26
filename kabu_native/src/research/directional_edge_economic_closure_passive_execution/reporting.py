"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.directional_edge_economic_closure_passive_execution.constants import (
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
            return obj[:2000]
        return [{"value": v} for v in obj[:2000]]
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            if isinstance(v, dict):
                row = {"key": k, **{sk: (json.dumps(sv, ensure_ascii=False, default=str) if isinstance(sv, (dict, list)) else sv) for sk, sv in v.items()}}
                out.append(row)
            else:
                out.append({"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v})
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
        "source_reproduction": _rows(p.get("reproduction")),
        "economic_formula": _rows(p.get("economic_formula")),
        "manual_yen_checks": _rows(p.get("manual_yen_checks")),
        "yen_vs_bps": _rows(p.get("yen_vs_bps")),
        "immediate_cross": _rows(p.get("immediate_cross_rows") or p.get("immediate_cross")),
        "horizon_comparison": _rows(p.get("horizon_comparison")),
        "fixed_candidate_spread_cohorts": _rows(p.get("cohort_rows") or p.get("spread_cohorts")),
        "execution_arms": _rows(p.get("execution_arms_rows") or p.get("execution_arms")),
        "orders": _rows(p.get("orders")),
        "fills": _rows(p.get("fills")),
        "partial_fills": _rows(p.get("partial_fills")),
        "no_fills": _rows(p.get("no_fills")),
        "queue_audit": _rows(p.get("queue_audit")),
        "train_arm_selection": _rows(p.get("train_arm_selection")),
        "validation": _rows(p.get("validation")),
        "holdout": _rows(p.get("holdout")),
        "daily": _rows(p.get("daily_rows") or p.get("daily")),
        "symbols": _rows(p.get("symbol_rows") or p.get("symbols")),
        "trade_dependence": _rows(p.get("trade_dependence")),
        "symbol_dependence": _rows(p.get("symbol_dependence")),
        "price_band": _rows(p.get("price_band")),
        "notional_band": _rows(p.get("notional_band")),
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
        "# DEECPA — Directional Edge Economic Closure + Passive Execution Audit",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('60_final_verdict') or (payload.get('verdict') or {}).get('final_verdict')}",
        f"- candidate: {c.get('2_fixed_candidate')}",
        f"- threshold: {c.get('3_fixed_threshold')}",
        f"- Immediate Gate E0: {c.get('16_Immediate_Gate_E0')}",
        f"- TRAIN arm: {c.get('35_TRAIN_fixed_arm')}",
        f"- VAL: {c.get('41_VAL_verdict')}",
        f"- HOLDOUT: {c.get('42_HOLDOUT_run')}",
        f"- submit/cancel/live: {c.get('58_submit_cancel_live')}",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
