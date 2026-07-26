"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_fcr_exact_method.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
        return obj if isinstance(obj[0], dict) else [{"value": v} for v in obj]
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
    arms = p.get("arm_results") or {}
    return {
        "README": [{"phase": "canonical_fcr_exact_method", "run_id": p.get("run_id")}],
        "SOURCE_AUDIT": _rows(p.get("source_audit")),
        "DATA_SPLIT": _rows(p.get("split")),
        "CANONICAL_COVERAGE": _rows(p.get("coverage")),
        "VWAP_AUDIT": _rows(p.get("vwap_audit")),
        "TREND_CONTEXT": _rows({"n": (p.get("counts") or {}).get("trend_context")}),
        "INITIAL_IMPULSES": _rows({"n": (p.get("counts") or {}).get("initial_impulses")}),
        "PULLBACKS": _rows({"n": (p.get("counts") or {}).get("pullbacks")}),
        "PULLBACK_QUALITY": _rows({"n": (p.get("counts") or {}).get("valid_pullbacks")}),
        "SELLING_EXHAUSTION": _rows({"n": (p.get("counts") or {}).get("selling_exhaustion")}),
        "BUY_FLOW_RESUMPTION": _rows({"n": (p.get("counts") or {}).get("buy_flow")}),
        "BOARD_FLOW_CONFIRMATION": _rows(p.get("board_flow_note")),
        "RECLAIM_LEVELS": _rows({"n": (p.get("counts") or {}).get("reclaim_levels")}),
        "RECLAIM_TRIGGERS": _rows({"n": (p.get("counts") or {}).get("reclaim_triggers")}),
        "STATE_TRANSITIONS": _rows(p.get("state_note")),
        "EPISODES": _rows(p.get("episode_stats")),
        "EXPIRED_EPISODES": _rows({"n": (p.get("episode_stats") or {}).get("expired")}),
        "INVALIDATED_EPISODES": _rows({"n": (p.get("episode_stats") or {}).get("invalidated")}),
        "ONE_IMPULSE_ONE_ENTRY": _rows(p.get("one_impulse")),
        "F0_RECLAIM_ONLY": _rows(arms.get("F0_RECLAIM_ONLY")),
        "F1_TREND_RECLAIM": _rows(arms.get("F1_TREND_RECLAIM")),
        "F2_PULLBACK_RECLAIM": _rows(arms.get("F2_PULLBACK_RECLAIM")),
        "F3_SELLING_EXHAUSTED": _rows(arms.get("F3_SELLING_EXHAUSTED")),
        "F4_BUY_FLOW_CONFIRMED": _rows(arms.get("F4_BUY_FLOW_CONFIRMED")),
        "F5_FULL_FCR": _rows(arms.get("F5_FULL_FCR")),
        "D1_NO_EXHAUSTION": _rows(arms.get("D1_NO_EXHAUSTION")),
        "D2_NO_BUY_FLOW": _rows(arms.get("D2_NO_BUY_FLOW")),
        "INCREMENTAL_EFFECTS": _rows(p.get("incremental")),
        "TRAIN_RESULTS": _rows(p.get("train_results")),
        "VALIDATION_RESULTS": _rows(p.get("val_results")),
        "FORENSIC_HOLDOUT": _rows(p.get("holdout_results")),
        "OPPORTUNITY_PATHS": _rows(p.get("opportunity_note")),
        "PBV2_MATCHED": _rows(p.get("pbv2_compare")),
        "EXECUTION_E0_E5": _rows((p.get("execution") or {}).get("E0_E5")),
        "ONE_TICK_ADVERSE": _rows((p.get("execution") or {}).get("one_tick_adverse")),
        "REFERENCE_EXITS": _rows(p.get("reference_exits")),
        "CAP5": _rows(p.get("cap5")),
        "CAP_BLOCKED": _rows(p.get("cap_blocked")),
        "SYMBOL_REENTRY": _rows(p.get("symbol_reentry")),
        "DAILY_RESULTS": _rows(p.get("daily_results")),
        "SYMBOL_RESULTS": _rows(p.get("symbol_results")),
        "DEPENDENCY": _rows(p.get("dependency")),
        "TESTS": _rows((p.get("tests") or {}).get("rows")),
        "VERDICT": _rows(p.get("verdict")),
    }


def write_md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    return "\n".join([
        "# Canonical Flow Confirmed Reclaim Entry (FCR)",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final_verdict: **{v.get('final_verdict')}**",
        f"- entry_verdict: `{v.get('entry_verdict')}`",
        "",
        "## Direction (fixed)",
        "Trend → normal pullback → selling exhaustion → buy flow → reclaim → ENTRY",
        "",
        f"## Thresholds: {p.get('thresholds')}",
        f"## Incremental: {json.dumps(p.get('incremental'), default=str)[:2500]}",
        f"## Train F5: {p.get('train_results', {}).get('F5_FULL_FCR')}",
        f"## Execution: {json.dumps(p.get('execution'), default=str)[:1200]}",
        f"submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        "",
    ])


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
