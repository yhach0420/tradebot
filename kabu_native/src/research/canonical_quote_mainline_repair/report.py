"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_quote_mainline_repair.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook

    def _cell(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool, str)):
            return v
        if isinstance(v, datetime):
            return v.isoformat()
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name in REQUIRED_SHEETS:
        rows = list(sheets.get(name) or [{"status": "empty"}])
        ws = wb.create_sheet(str(name)[:31])
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
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


def _rows(obj: Any) -> list[dict[str, Any]]:
    if obj is None:
        return [{"status": "empty"}]
    if isinstance(obj, list):
        if not obj:
            return [{"status": "empty"}]
        if isinstance(obj[0], dict):
            return obj
        return [{"value": v} for v in obj]
    if isinstance(obj, dict):
        return [
            {"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v}
            for k, v in obj.items()
        ]
    return [{"value": str(obj)}]


def _sheets(payload: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dual = payload.get("dual") or {}
    gates = payload.get("gates") or {}
    raw = payload.get("raw_scan") or {}
    return {
        "README": [{"phase": "canonical_quote_mainline_repair", "run_id": payload.get("run_id"), "sot_audit": payload.get("audit_sot")}],
        "SOURCE_AUDIT": _rows(payload.get("source_audit")),
        "CANONICAL_SPEC": _rows(payload.get("canonical_spec")),
        "RAW_FIELD_PRESERVATION": _rows(payload.get("raw_preservation")),
        "RUNTIME_REFERENCE_CLOSURE": _rows(raw.get("hits") or [{"hard": raw.get("hard_direct_refs")}]),
        "STAGE0_LINEAGE": _rows(payload.get("stage0")),
        "TOP_IMBALANCE": _rows(payload.get("top_imbalance")),
        "DEPTH_IMBALANCE": _rows(payload.get("depth_imbalance")),
        "BOARD_CLASSIFICATION": _rows(dual.get("board_classification")),
        "LEGACY_PARITY": _rows({
            "pass": gates.get("LEGACY_RUNTIME_PARITY_PASS"),
            "note": gates.get("legacy_parity_note"),
            "deterministic_p0": dual.get("deterministic_p0"),
        }),
        "ENTRY_DECISION_DIFF": _rows(dual.get("entry_diff")),
        "EXIT_DECISION_DIFF": _rows(dual.get("exit_diff_sample")),
        "ENTRY_TRACE": _rows(dual.get("entry_traces")),
        "EXIT_TRACE": _rows(dual.get("exit_diff_sample")),
        "P0_LEGACY": _rows(dual.get("P0")),
        "P1_CANONICAL_ENTRY": _rows(dual.get("P1")),
        "P2_CANONICAL_EXIT": _rows(dual.get("P2")),
        "P3_CANONICAL_FULL": _rows(dual.get("P3")),
        "CAP5_EVENT_LOG": _rows((dual.get("P3") or {}).get("event_log_sample")),
        "PORTFOLIO_RESULTS": [
            {"portfolio": k, **{kk: vv for kk, vv in (dual.get(k) or {}).items() if kk not in ("event_log_sample", "trade_sample", "daily_pnl", "leave_one_day_out_pf")}}
            for k in ("P0", "P1", "P2", "P3")
        ],
        "DAILY_RESULTS": [
            {"portfolio": k, "day": d, "pnl": v}
            for k in ("P0", "P1", "P2", "P3")
            for d, v in ((dual.get(k) or {}).get("daily_pnl") or {}).items()
        ] or [{"status": "empty"}],
        "SYMBOL_DEPENDENCY": [
            {"portfolio": k, "symbol": s, "pnl": p}
            for k in ("P0", "P1", "P2", "P3")
            for s, p in ((dual.get(k) or {}).get("top_symbols") or [])
        ] or [{"status": "empty"}],
        "DAY_DEPENDENCY": [
            {"portfolio": k, "left_out_day": d, "pf": pf}
            for k in ("P0", "P1", "P2", "P3")
            for d, pf in ((dual.get(k) or {}).get("leave_one_day_out_pf") or {}).items()
        ] or [{"status": "empty"}],
        "EXECUTION_PRICE": _rows(payload.get("execution_price")),
        "OPERATIONAL_EXITS": _rows(payload.get("operational_exits")),
        "INVALIDATED_HISTORY": _rows(payload.get("invalidated")),
        "PAPER_READINESS": _rows({"readiness": gates.get("paper_readiness"), **{k: gates.get(k) for k in gates if "PAPER" in k or "LIVE" in k or "EDGE" in k}}),
        "TESTS": _rows((payload.get("tests") or {}).get("rows")),
        "VERDICT": _rows(gates),
    }


def _md(payload: Mapping[str, Any]) -> str:
    g = payload.get("gates") or {}
    d = payload.get("dual") or {}
    lines = [
        "# Canonical Quote Mainline Repair",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- paper_readiness: **{g.get('paper_readiness')}**",
        f"- integrity: `{ 'PASS' if g.get('CANONICAL_RUNTIME_INTEGRITY_PASS') else 'BLOCKED' }`",
        f"- legacy_parity: `{ 'PASS' if g.get('LEGACY_RUNTIME_PARITY_PASS') else 'BLOCKED' }`",
        f"- edge: `{g.get('edge_code')}`",
        "",
        "## Portfolio CAP=5",
        "",
    ]
    for k in ("P0", "P1", "P2", "P3"):
        p = d.get(k) or {}
        lines.append(
            f"- {k}: trades={p.get('trades')} pnl={p.get('pnl_5bps')} PF={p.get('PF_5bps')} "
            f"stop={p.get('stop_rate')} NP={p.get('no_progress_rate')}"
        )
    lines += [
        "",
        f"- submit={payload.get('submit')} cancel={payload.get('cancel')} live_order={payload.get('live_order')}",
        f"- auto_paper_start=False live_trading_blocked=True",
        "",
    ]
    return "\n".join(lines)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
