"""Read existing report.json artifacts only. No harvest. No Capture. No day-grid reconstruction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.selection_surface_mechanism_rca_v1.isolation import RESEARCH_ROOT

SOURCE_SPECS = (
    {
        "ANALYSIS_ID": "GENERALIZATION_FAILURE_COMPONENT_RCA_V1",
        "dirname": "generalization_failure_component_rca_v1",
        "role": "HARD_PIN",
    },
    {
        "ANALYSIS_ID": "SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1",
        "dirname": "systematic_state_transition_full_strategy_v1",
        "role": "ST_STABILITY_SOURCE",
    },
)


def report_path(dirname: str) -> Path:
    return RESEARCH_ROOT / dirname / "report.json"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load_report(dirname: str) -> dict[str, Any]:
    path = report_path(dirname)
    if not path.is_file():
        raise FileNotFoundError(f"MISSING_SOURCE_REPORT {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_sources() -> dict[str, Any]:
    items = []
    by_id: dict[str, dict[str, Any]] = {}
    for spec in SOURCE_SPECS:
        path = report_path(str(spec["dirname"]))
        report = load_report(str(spec["dirname"]))
        aid = str(report.get("ANALYSIS_ID") or "")
        if aid != spec["ANALYSIS_ID"]:
            raise RuntimeError(f"SOURCE_ANALYSIS_ID_MISMATCH {spec['ANALYSIS_ID']} got {aid}")
        rec = {**spec, "path": str(path), "report_sha256": file_sha256(path), "report": report}
        items.append(rec)
        by_id[aid] = rec
    payload = [{"ANALYSIS_ID": r["ANALYSIS_ID"], "report_sha256": r["report_sha256"]} for r in items]
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "items": items,
        "by_id": by_id,
        "inventory_fingerprint": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def answers(report: dict[str, Any]) -> dict[str, Any]:
    return dict(report.get("answers") or {})
