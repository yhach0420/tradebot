"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.upward_edge_identification_audit.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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
    fd = p.get("feature_distribution") or {}
    return {
        "summary": _rows(p.get("verdict")),
        "dataset_scope": _rows(p.get("dataset_scope")),
        "data_quality": _rows(p.get("data_quality")),
        "sample_population": _rows(p.get("sample_population")),
        "barrier_definitions": _rows(p.get("barriers")),
        "labels": _rows(p.get("label_counts")),
        "feature_dictionary": _rows(p.get("feature_availability")),
        "feature_distribution": _rows(fd),
        "price_state": _rows({k: v for k, v in fd.items() if k.startswith("G1_")}),
        "aggressive_flow": _rows({k: v for k, v in fd.items() if k.startswith("G2_")}),
        "flow_efficiency": _rows({k: v for k, v in fd.items() if k.startswith("G3_")}),
        "persistence": _rows({k: v for k, v in fd.items() if k.startswith("G4_")}),
        "market_context": _rows({k: v for k, v in fd.items() if k.startswith("G5_")}),
        "remaining_upside": _rows({k: v for k, v in fd.items() if k.startswith("G6_")}),
        "univariate_bins": _rows(p.get("univariate_results")),
        "hypothesis_comparison": _rows(p.get("hypothesis_results")),
        "model_metrics_train": _rows(p.get("train_metrics")),
        "model_metrics_validation": _rows(p.get("validation_metrics")),
        "model_metrics_holdout": _rows(p.get("holdout_metrics")),
        "first_passage_summary": _rows(p.get("first_passage_summary")),
        "daily_metrics": _rows(p.get("daily_metrics")),
        "symbol_metrics": _rows(p.get("symbol_metrics")),
        "winner_vs_down": _rows(p.get("winner_vs_down")),
        "high_buy_no_rise": _rows(p.get("high_buy_no_rise")),
        "high_replenish_no_rise": _rows(p.get("high_replenish_no_rise")),
        "pbv2_comparison": _rows(p.get("pbv2_comparison")),
        "duplicate_overlap_audit": _rows(p.get("duplicate_overlap_audit")),
        "execution_audit": _rows(p.get("execution_audit")),
        "integrity_audit": _rows(p.get("integrity")),
        "tests": _rows((p.get("tests") or {}).get("rows") or p.get("tests")),
    }


def emit(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    c = payload.get("completion") or {}
    md = [
        "# Upward Edge Identification Audit (UEIA)",
        "",
        f"- run_id: {payload.get('run_id')}",
        f"- final: {c.get('59_final_verdict')}",
        f"- days: {c.get('1_data_period')}",
        f"- samples: {c.get('5_sample_n')}",
        f"- best hypothesis: {c.get('28_best_hypothesis')}",
        f"- VAL AUC (B2): {c.get('30_val_auc')}",
        f"- edge status: {c.get('50_edge_status')}",
        f"- causes: {c.get('51_failure_causes')}",
        "",
        "Identification audit only. Not a trading strategy.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", build_sheets(payload))
    present = sorted(x.name for x in out_dir.iterdir() if x.is_file())
    assert tuple(present) == tuple(sorted(REQUIRED_ARTIFACTS)), present
