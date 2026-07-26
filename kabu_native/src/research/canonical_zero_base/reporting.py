"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_zero_base.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
    lanes = payload.get("lanes") or {}
    return {
        "README": [{"phase": "canonical_zero_base_strategy", "run_id": payload.get("run_id")}],
        "SOURCE_AUDIT": _rows(payload.get("source_audit")),
        "DATA_DISCOVERY": _rows(payload.get("discovery", {}).get("audits")),
        "DATA_SPLIT": _rows({
            "warmup": payload.get("discovery", {}).get("warmup"),
            "train": payload.get("discovery", {}).get("train"),
            "validation": payload.get("discovery", {}).get("validation"),
            "strict_oos": payload.get("discovery", {}).get("strict_oos"),
            "insufficient": payload.get("discovery", {}).get("insufficient_oos"),
        }),
        "CANONICAL_COVERAGE": _rows({
            "ask": payload.get("discovery", {}).get("ask_coverage_mean"),
            "bid": payload.get("discovery", {}).get("bid_coverage_mean"),
        }),
        "FEATURE_DICTIONARY": _rows(payload.get("feature_dictionary")),
        "FEATURE_QUALITY": _rows(payload.get("feature_quality")),
        "EPISODES": _rows(payload.get("episode_stats")),
        "OPPORTUNITY_LABELS": _rows(payload.get("opportunity_summary")),
        "Z1_CONTRACT": _rows(payload.get("contracts", {}).get("Z1")),
        "Z2_CONTRACT": _rows(payload.get("contracts", {}).get("Z2")),
        "Z3_CONTRACT": _rows(payload.get("contracts", {}).get("Z3")),
        "Z4_CONTRACT": _rows(payload.get("contracts", {}).get("Z4")),
        "COMBINATION_COUNTS": [
            {"strategy": k, "raw": v.get("raw_combinations"), "train_pass": v.get("train_pass"), "val_pass": v.get("val_pass"), "oos_carry": v.get("oos_carry")}
            for k, v in lanes.items()
        ] or [{"status": "empty"}],
        "TRAIN_RESULTS": [x for k, v in lanes.items() for x in (v.get("train_top") or [])] or [{"status": "empty"}],
        "VALIDATION_RESULTS": [x for k, v in lanes.items() for x in (v.get("val_top") or [])] or [{"status": "empty"}],
        "STRICT_OOS_RESULTS": [x for k, v in lanes.items() for x in (v.get("oos_results") or [])] or [{"status": "empty"}],
        "ENTRY_RESULTS": _rows(payload.get("entry_results")),
        "EXIT_RESULTS": _rows(payload.get("exit_results")),
        "ENTRY_EXIT_PAIR": _rows(payload.get("pair_results")),
        "EXECUTION_SCENARIOS": _rows(payload.get("execution_scenarios")),
        "ONE_EPISODE_ONE_ENTRY": _rows(payload.get("one_episode")),
        "CAP5_Z1": _rows((lanes.get("Z1") or {}).get("final", {}).get("cap") if (lanes.get("Z1") or {}).get("final") else {"empty": True}),
        "CAP5_Z2": _rows((lanes.get("Z2") or {}).get("final", {}).get("cap") if (lanes.get("Z2") or {}).get("final") else {"empty": True}),
        "CAP5_Z3": _rows((lanes.get("Z3") or {}).get("final", {}).get("cap") if (lanes.get("Z3") or {}).get("final") else {"empty": True}),
        "CAP5_Z4": _rows((lanes.get("Z4") or {}).get("final", {}).get("cap") if (lanes.get("Z4") or {}).get("final") else {"empty": True}),
        "CAP5_INTEGRATED": _rows(payload.get("cap5_integrated")),
        "DAILY_RESULTS": _rows(payload.get("daily_results")),
        "SYMBOL_RESULTS": _rows(payload.get("symbol_results")),
        "DEPENDENCY": _rows(payload.get("dependency_summary")),
        "LEAVE_ONE_OUT": _rows(payload.get("leave_one_out")),
        "OVERFIT_GATES": _rows(payload.get("overfit_gates")),
        "LEGACY_REFERENCE": _rows(payload.get("legacy_reference")),
        "CANDIDATE_SELECTION": _rows(payload.get("candidate_selection")),
        "TESTS": _rows((payload.get("tests") or {}).get("rows")),
        "VERDICT": _rows(payload.get("verdict")),
    }


def _md(payload: Mapping[str, Any]) -> str:
    v = payload.get("verdict") or {}
    d = payload.get("discovery") or {}
    lines = [
        "# Canonical Zero-Base Entry–Exit Strategy Rebuild",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- final_verdict: **{v.get('final_verdict')}**",
        f"- insufficient_oos: `{d.get('insufficient_oos')}`",
        f"- train: {d.get('train')} val: {d.get('validation')} oos: {d.get('strict_oos')}",
        "",
        "## Lane OOS CAP5",
        "",
    ]
    for k, lane in (payload.get("lanes") or {}).items():
        fin = lane.get("final") or {}
        cap = fin.get("cap") or {}
        lines.append(f"- {k}: pnl={cap.get('pnl_5bps')} PF={cap.get('PF_5bps')} trades={cap.get('trades')} tmpl={(fin.get('rule') or {}).get('template')}")
    lines += [
        "",
        f"- integrated: {(payload.get('cap5_integrated') or {}).get('pnl_5bps')}",
        f"- submit={payload.get('submit')} cancel={payload.get('cancel')} live_order={payload.get('live_order')}",
        "",
    ]
    return "\n".join(lines)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
    # strip heavy trades from json
    slim = json.loads(json.dumps(payload, default=str))
    for lane in (slim.get("lanes") or {}).values():
        fin = lane.get("final")
        if isinstance(fin, dict) and "trades" in fin:
            fin["trades_n"] = len(fin.get("trades") or [])
            fin.pop("trades", None)
    (out_dir / "report.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
