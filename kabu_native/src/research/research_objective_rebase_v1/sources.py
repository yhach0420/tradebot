"""Read existing report.json artifacts only. No harvest. No Capture/Stress."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.research_objective_rebase_v1.isolation import RESEARCH_ROOT
from research.research_objective_rebase_v1.spec import ARCHITECTURE_TO_LINEAGE

SOURCE_SPECS = (
    {
        "ANALYSIS_ID": "NEW_ARCHITECTURE_CLASS_RETHINK_V1",
        "dirname": "new_architecture_class_rethink_v1",
        "role": "ARCHITECTURE_SPACE",
        "lineage": None,
        "overlay": False,
    },
    {
        "ANALYSIS_ID": "SIMPLE_FULL_STRATEGY_DISCOVERY_V1",
        "dirname": "simple_full_strategy_discovery_v1",
        "role": "L1_STUDY",
        "lineage": "L1_TECHNICAL_PRICE_STATE",
        "overlay": False,
    },
    {
        "ANALYSIS_ID": "E4_X2_Z3_CAUSAL_CONCENTRATION_RECHECK_V1",
        "dirname": "e4_x2_z3_causal_concentration_recheck_v1",
        "role": "L1_CAUSAL_RECHECK",
        "lineage": "L1_TECHNICAL_PRICE_STATE",
        "overlay": False,
    },
    {
        "ANALYSIS_ID": "SYSTEMATIC_STATE_TRANSITION_FULL_STRATEGY_V1",
        "dirname": "systematic_state_transition_full_strategy_v1",
        "role": "L1_STUDY",
        "lineage": "L1_TECHNICAL_PRICE_STATE",
        "overlay": False,
    },
    {
        "ANALYSIS_ID": "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2",
        "dirname": "c1_multi_timeframe_entry_exit_full_strategy_v2",
        "role": "L1_STUDY",
        "lineage": "L1_TECHNICAL_PRICE_STATE",
        "overlay": False,
    },
    {
        "ANALYSIS_ID": "RECOVERY_SEQUENCE_FULL_STRATEGY_ARCHITECTURE_V1",
        "dirname": "recovery_sequence_full_strategy_architecture_v1",
        "role": "L2_STUDY",
        "lineage": "L2_RECOVERY_RECLAIM_PATH",
        "overlay": False,
    },
    {
        "ANALYSIS_ID": "PARTICIPATION_ONSET_FULL_STRATEGY_V1",
        "dirname": "participation_onset_full_strategy_v1",
        "role": "L3_STUDY",
        "lineage": "L3_ACTIVITY_ONSET",
        "overlay": False,
    },
    {
        "ANALYSIS_ID": "C4_PORTFOLIO_CROWDING_FULL_STRATEGY_V2",
        "dirname": "c4_portfolio_crowding_full_strategy_v2",
        "role": "OVERLAY",
        "lineage": "O1_PORTFOLIO_CROWDING_OVERLAY",
        "overlay": True,
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
        rec = {
            **spec,
            "path": str(path),
            "report_sha256": file_sha256(path),
            "verdict": _verdict(report),
            "report": report,
        }
        items.append(rec)
        by_id[aid] = rec
    return {
        "items": items,
        "by_id": by_id,
        "architecture_to_lineage": list(ARCHITECTURE_TO_LINEAGE),
        "inventory_fingerprint": _inventory_fingerprint(items),
    }


def _verdict(report: dict[str, Any]) -> str | None:
    answers = dict(report.get("answers") or {})
    decision = dict(report.get("decision") or {})
    for key in ("VERDICT", "verdict", "36_verdict", "56_verdict", "52_verdict"):
        if answers.get(key) is not None:
            return str(answers.get(key))
        if decision.get(key) is not None:
            return str(decision.get(key))
    return None


def _inventory_fingerprint(items: list[dict[str, Any]]) -> str:
    payload = [
        {
            "ANALYSIS_ID": r["ANALYSIS_ID"],
            "report_sha256": r["report_sha256"],
            "role": r["role"],
            "lineage": r["lineage"],
        }
        for r in items
    ]
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def ranking(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(report.get("ranking") or [])
    if rows:
        return rows
    answers = dict(report.get("answers") or {})
    table = answers.get("22_complete_ranking_table") or answers.get("13_complete_ranking_table")
    return list(table or [])


def answers(report: dict[str, Any]) -> dict[str, Any]:
    return dict(report.get("answers") or {})
