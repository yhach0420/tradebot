"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_strategy_root_cause.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
    a = payload.get("analysis") or {}
    return {
        "README": [{"phase": "canonical_strategy_root_cause", "run_id": payload.get("run_id"), "sot": payload.get("sot")}],
        "SOURCE_AUDIT": _rows(payload.get("source_audit")),
        "PARITY_STATUS": _rows(a.get("parity")),
        "ENTRY_COHORTS": _rows(a.get("cohort_counts")),
        "PRE_EXIT_OPPORTUNITY": [
            v for v in (a.get("opportunity") or {}).values() if isinstance(v, dict)
        ] or [{"status": "empty"}],
        "BOARD_QUANTILES": _rows(a.get("board_quantiles")),
        "EXIT_CONTROLS": [
            {"exit_mode": k, **{kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict)) or kk == "exit_class_mix"}}
            for k, v in (a.get("exit_controls") or {}).items()
        ] or [{"status": "empty"}],
        "EXIT_REASON_AUDIT": _rows(a.get("exit_audit_sample")),
        "IMMEDIATE_EXIT": _rows(a.get("immediate_exit")),
        "SPREAD_STOP": _rows((a.get("spread_stop") or {}).get("sample")),
        "EPISODES": _rows(a.get("episodes")),
        "REENTRY": _rows({
            k: {kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict))} if isinstance(v, dict) else v
            for k, v in (a.get("reentry") or {}).items()
        }),
        "C0_C8_RESULTS": [
            {"arm": k, "mode": "event", **{kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict)) or kk == "exit_class_mix"}}
            for k, v in (a.get("C_event") or {}).items()
        ] + [
            {"arm": k, "mode": "episode", **{kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict)) or kk == "exit_class_mix"}}
            for k, v in (a.get("C_episode") or {}).items()
        ] or [{"status": "empty"}],
        "CAP5_EVENT_LOG": [{"note": "summary in C0_C8_RESULTS; sample logs truncated for artifact size"}],
        "DAILY_RESULTS": [{"note": "see C_event daily_pnl in report.json if present"}],
        "SYMBOL_RESULTS": [{"note": "see C_event top_symbols in report.json if present"}],
        "ROOT_CAUSE_ATTRIBUTION": _rows({"primary": a.get("primary_root_cause"), "causes": a.get("causes"), **(a.get("attribution") or {})}),
        "NEXT_DECISION": [{"decision": d} for d in (a.get("decisions") or [])] or [{"status": "empty"}],
        "TESTS": _rows((payload.get("tests") or {}).get("rows")),
        "VERDICT": _rows(payload.get("verdict")),
    }


def _md(payload: Mapping[str, Any]) -> str:
    a = payload.get("analysis") or {}
    v = payload.get("verdict") or {}
    lines = [
        "# Canonical Strategy Root Cause Closure",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- primary_root_cause: **{a.get('primary_root_cause')}**",
        f"- final_verdict: **{v.get('final_verdict')}**",
        f"- determinism: `{a.get('determinism_pass')}`",
        f"- runtime_parity: `LEGACY_RUNTIME_PARITY_NOT_EVALUABLE`",
        "",
        "## Opportunity",
        "",
    ]
    for k, o in (a.get("opportunity") or {}).items():
        lines.append(
            f"- {k}: n={o.get('n')} mfe5m={o.get('avg_mfe_5m')} never_prof={o.get('never_profitable_rate')} stop5m={o.get('stop_5m_rate')}"
        )
    lines += ["", "## C0–C8 (event)", ""]
    for k, r in (a.get("C_event") or {}).items():
        lines.append(f"- {k}: trades={r.get('trades')} pnl={r.get('pnl_5bps')} PF={r.get('PF_5bps')}")
    lines += [
        "",
        f"- decisions: {', '.join(a.get('decisions') or [])}",
        f"- submit={payload.get('submit')} cancel={payload.get('cancel')} live_order={payload.get('live_order')}",
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
