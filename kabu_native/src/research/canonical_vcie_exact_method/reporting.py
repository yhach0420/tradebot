"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_vcie_exact_method.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
            return obj
        return [{"value": v} for v in obj]
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
    arms = p.get("arm_results") or {}
    return {
        "README": [{"phase": "canonical_vcie_exact_method", "run_id": p.get("run_id")}],
        "SOURCE_AUDIT": _rows(p.get("source_audit")),
        "VOLUME_LINEAGE": _rows((p.get("lineage") or {}).get("volume")),
        "TRADE_DIRECTION_LINEAGE": _rows((p.get("lineage") or {}).get("trade_direction")),
        "SESSION_TIME_LINEAGE": _rows((p.get("lineage") or {}).get("session_time")),
        "CANONICAL_EXECUTION": _rows((p.get("lineage") or {}).get("execution")),
        "DATA_SPLIT": _rows(p.get("split")),
        "CONTEXT_EVENTS": _rows(p.get("event_counts")),
        "VOLUME_BURSTS": _rows({"n": (p.get("event_counts") or {}).get("volume_bursts")}),
        "TRADE_SIDE_EVENTS": _rows({"n": (p.get("event_counts") or {}).get("trade_side_confirmations")}),
        "PRICE_CROSSES": _rows({"n": (p.get("event_counts") or {}).get("price_crosses")}),
        "BREAKOUT_HOLDS": _rows({"n": (p.get("event_counts") or {}).get("breakout_holds")}),
        "EPISODES": _rows(p.get("episode_stats")),
        "EXPIRED_EPISODES": _rows({"n": (p.get("episode_stats") or {}).get("expired")}),
        "FAILED_EPISODES": _rows({"n": (p.get("episode_stats") or {}).get("failed")}),
        "V1_PRICE_CROSS": _rows(arms.get("V1_PRICE_CROSS")),
        "V2_VOLUME": _rows(arms.get("V2_VOLUME_CONFIRMED")),
        "V3_TRADE_SIDE": _rows(arms.get("V3_TRADE_SIDE_CONFIRMED")),
        "V4_FULL_VCIE": _rows(arms.get("V4_FULL_VCIE")),
        "D1_TRADE_SIDE_NO_VOLUME": _rows(arms.get("D1_PRICE_PLUS_TRADE_SIDE")),
        "INCREMENTAL_EFFECTS": _rows(p.get("incremental")),
        "TRAIN_RESULTS": _rows(p.get("train_results")),
        "VALIDATION_RESULTS": _rows(p.get("val_results")),
        "STRICT_OOS": _rows(p.get("oos_results")),
        "OPPORTUNITY_PATHS": _rows(p.get("opportunity_note")),
        "EXECUTION_E0_E5": _rows((p.get("execution") or {}).get("E0_E5")),
        "ONE_TICK_ADVERSE": _rows((p.get("execution") or {}).get("one_tick_adverse")),
        "CAP5": _rows(p.get("cap5")),
        "CAP_BLOCKED": _rows(p.get("cap_blocked")),
        "DAILY_RESULTS": _rows(p.get("daily_results")),
        "SYMBOL_RESULTS": _rows(p.get("symbol_results")),
        "DEPENDENCY": _rows(p.get("dependency")),
        "TESTS": _rows((p.get("tests") or {}).get("rows")),
        "VERDICT": _rows(p.get("verdict")),
    }


def write_md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    thr = p.get("thresholds") or {}
    inc = p.get("incremental") or {}
    lines = [
        "# Canonical VCIE Rebuild — Exact Yesterday Method",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final_verdict: **{v.get('final_verdict')}**",
        "",
        "## Method (unchanged from yesterday)",
        "",
        "1. Identify gaps (price-only / no volume / no trade-side / stale candidates)",
        "2. Fix one direction: volume+buy-flow then high cross then hold",
        "3. Minimal observations only",
        "4. Incremental arms V1→V2→V3→V4 (+ D1 diagnostic)",
        "5. Compare increments",
        "6. Absolute TRAIN/VALIDATION gates",
        "7. EXIT research blocked until ENTRY validates",
        "",
        f"## Thresholds (TRAIN): {thr}",
        "",
        f"## Incremental: {json.dumps(inc, default=str)[:2000]}",
        "",
        f"## Lineage: {json.dumps(p.get('lineage'), default=str)[:1500]}",
        "",
        f"## Arms TRAIN: {json.dumps(p.get('train_results'), default=str)[:2000]}",
        "",
        f"## Execution: {json.dumps(p.get('execution'), default=str)[:1500]}",
        "",
        f"submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')} mainline_changed={p.get('mainline_changed')}",
        "",
    ]
    return "\n".join(lines)


def emit(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in list(out_dir.iterdir()):
        if f.is_file() and f.name not in REQUIRED_ARTIFACTS:
            f.unlink()
    slim = json.loads(json.dumps(payload, default=str))
    (out_dir / "report.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(write_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    for f in list(out_dir.iterdir()):
        if f.is_file() and f.name not in REQUIRED_ARTIFACTS:
            f.unlink()
