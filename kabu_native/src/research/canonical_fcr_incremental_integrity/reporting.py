"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.canonical_fcr_incremental_integrity.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
            return obj[:500]
        return [{"value": v} for v in obj[:500]]
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
    train = p.get("train_results") or {}
    return {
        "README": [{"phase": "canonical_fcr_incremental_integrity", "run_id": p.get("run_id")}],
        "SOURCE_AUDIT": _rows(p.get("source_audit")),
        "OLD_RUN_BASELINE": _rows(p.get("old_baseline")),
        "STRIDE_AUDIT": _rows(p.get("stride_audit")),
        "EVENT_COUNT_RECONCILIATION": _rows(p.get("event_reconciliation")),
        "EVENT_SEQUENCE_GAPS": _rows(p.get("seq_gaps")),
        "EPISODE_LINEAGE": _rows(p.get("episode_lineage")),
        "RECLAIM_CANDIDATES": _rows(p.get("reclaim_sample")),
        "COMMON_ANCHORS": _rows(p.get("common_anchor_audit")),
        "PARENT_LINEAGE": _rows(p.get("parent_lineage")),
        "ARM_MEMBERSHIP": _rows(p.get("arm_counts")),
        "ARM_NESTING": _rows(p.get("arm_nesting")),
        "STATE_STAGE_NESTING": _rows(p.get("state_stage")),
        "F5_SPEC_AUDIT": _rows(p.get("f5_spec")),
        "SPREAD_GATE_AUDIT": _rows(p.get("f5_spec")),
        "MATCHED_F0": _rows(train.get("F0_RECLAIM_BASE")),
        "MATCHED_F1": _rows(train.get("F1_TREND")),
        "MATCHED_F2": _rows(train.get("F2_PULLBACK")),
        "MATCHED_F3": _rows(train.get("F3_EXHAUSTION")),
        "MATCHED_F4": _rows(train.get("F4_BUY_FLOW")),
        "MATCHED_F5": _rows(train.get("F5_FULL_FCR")),
        "MATCHED_INCREMENTAL": _rows(p.get("matched_incremental")),
        "NATIVE_TIMING_DIAGNOSTIC": _rows(p.get("native_timing")),
        "TRAIN_RESULTS": _rows(train),
        "EXECUTION": _rows(p.get("execution")),
        "SYMBOL_DEPENDENCY": _rows(p.get("symbol_dependency")),
        "OLD_VS_FIXED": _rows(p.get("old_vs_fixed")),
        "TESTS": _rows((p.get("tests") or {}).get("rows") or p.get("tests")),
        "VERDICT": _rows(p.get("verdict")),
    }


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    v = payload.get("verdict") or {}
    c = payload.get("completion") or {}
    md = [
        "# Canonical FCR Incremental Integrity Closure",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final_verdict: {v.get('final_verdict')}",
        f"- integrity: {v.get('integrity_verdict')}",
        f"- stride: {c.get('3_stride_meaning')}",
        f"- TRAIN F5: n={c.get('43_matched_F5_n')} edge={c.get('66_current_f5_edge')}",
        f"- VALIDATION: {c.get('67_validation')}",
        f"- EXIT: {c.get('69_exit')}",
        "",
        "Formal increments use MATCHED_COMMON_ANCHOR_INCREMENTAL only.",
        "Native trigger timing is diagnostic-only.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
