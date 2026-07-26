"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.integrated_order_flow_absorption_reversal.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
    sc = p.get("state_counts") or {}
    return {
        "summary": _rows(p.get("verdict")),
        "dataset_scope": _rows(p.get("dataset_scope")),
        "feature_distribution": _rows(p.get("feature_distribution")),
        "episodes": _rows(p.get("episode_stats")),
        "state_transitions": _rows(sc),
        "sell_pressure": _rows({"n": sc.get("S1_SELL_PRESSURE")}),
        "absorption": _rows({"n": sc.get("S2_ABSORPTION_ACTIVE")}),
        "sell_exhaustion": _rows({"n": sc.get("S3_SELL_EXHAUSTION")}),
        "buy_reversal": _rows({"n": sc.get("S4_BUY_FLOW_REVERSAL")}),
        "acceptance": _rows({"n": sc.get("S5_ACCEPTANCE_CONFIRM")}),
        "entries": _rows(p.get("entry_sample")),
        "post_entry_states": _rows({k: sc.get(k) for k in ("S6_DEMAND_CONTINUATION", "S7_ABSORPTION_FAILURE", "S8_NO_DEMAND_FOLLOW_THROUGH", "S9_DEMAND_EXHAUSTION", "S10_PROFIT_GIVEBACK")}),
        "exits": _rows(((p.get("train_arms") or {}).get("A5") or {}).get("reasons")),
        "arms": _rows(p.get("train_arms")),
        "incremental": _rows(p.get("incremental")),
        "outcome_classes": _rows(((p.get("train_arms") or {}).get("A5") or {}).get("outcomes")),
        "success_failure_comparison": _rows(p.get("success_failure")),
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
        "# Integrated Order Flow Absorption Reversal (IOAR)",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('51_final_verdict')}",
        f"- TRAIN days: {c.get('2_train_days')}",
        f"- ENTRY n: {c.get('13_entry_n')}",
        f"- A5 PF/PnL: {(c.get('18_pf') or {}).get('A5')} / {(c.get('19_pnl') or {}).get('A5')}",
        f"- cause: {c.get('42_train_fail_cause')}",
        "",
        "Sell absorption → buy reversal integrated scenario. Not a price-shape bounce.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
