"""Load NEW_ARCHITECTURE_CLASS_RETHINK_V1. Fail closed if prior decision drifted."""
from __future__ import annotations

import json
from typing import Any

from research.c1_multi_timeframe_precommit_v1 import (
    C4_CLASS,
    PRIOR_ANALYSIS_ID,
    PRIOR_SELECTED_REQUIRED,
    PRIOR_VERDICT_REQUIRED,
)
from research.c1_multi_timeframe_precommit_v1.isolation import RESEARCH_ROOT


def load_prior() -> dict[str, Any]:
    path = RESEARCH_ROOT / "new_architecture_class_rethink_v1" / "report.json"
    if not path.is_file():
        return {"ok": False, "blocker": "PRIOR_REPORT_MISSING", "path": str(path)}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "blocker": f"PRIOR_REPORT_UNREADABLE:{type(exc).__name__}", "path": str(path)}
    if not isinstance(obj, dict):
        return {"ok": False, "blocker": "PRIOR_REPORT_NOT_OBJECT", "path": str(path)}
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    verdict = str(d.get("VERDICT") or a.get("47_VERDICT") or "")
    selected = str(d.get("SELECTED_ARCHITECTURE_CLASS") or a.get("13_SELECTED_ARCHITECTURE_CLASS") or "")
    eligible = list(d.get("ELIGIBLE_CLASS_IDS") or [])

    def _int_eq(key: str, expected: int) -> bool:
        if key not in d:
            return False
        try:
            return int(d[key]) == int(expected)
        except (TypeError, ValueError):
            return False

    checks = {
        "ANALYSIS_ID": str(obj.get("ANALYSIS_ID") or d.get("ANALYSIS_ID") or "") == PRIOR_ANALYSIS_ID,
        "VERDICT": verdict == PRIOR_VERDICT_REQUIRED,
        "SELECTED": selected == PRIOR_SELECTED_REQUIRED,
        "C1_IN_ELIGIBLE": PRIOR_SELECTED_REQUIRED in eligible,
        "C4_IN_ELIGIBLE": C4_CLASS in eligible,
        "PNL_USED_TO_SELECT_CLASS": d.get("PNL_USED_TO_SELECT_CLASS") is False,
        "EVENT_COUNT_USED_TO_SELECT_CLASS": d.get("EVENT_COUNT_USED_TO_SELECT_CLASS") is False,
        "COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": _int_eq("COMPOSITE_ARCHITECTURE_EVENT_COUNT_N", 0),
        "NEW_TRIGGER_DEFINITION_N": _int_eq("NEW_TRIGGER_DEFINITION_N", 0),
        "NEW_THRESHOLD_DEFINITION_N": _int_eq("NEW_THRESHOLD_DEFINITION_N", 0),
        "CANDIDATE_LIBRARY_GENERATED": d.get("CANDIDATE_LIBRARY_GENERATED") is False,
    }
    failed = [k for k, v in checks.items() if not v]
    ok = not failed
    return {
        "ok": bool(ok),
        "blocker": None if ok else "PRIOR_DECISION_MISMATCH:" + ",".join(failed),
        "failed_checks": failed,
        "path": str(path),
        "ANALYSIS_ID": obj.get("ANALYSIS_ID"),
        "VERDICT": verdict,
        "SELECTED_ARCHITECTURE_CLASS": selected,
        "ELIGIBLE_CLASS_IDS": eligible,
        "PNL_USED_TO_SELECT_CLASS": d.get("PNL_USED_TO_SELECT_CLASS"),
        "EVENT_COUNT_USED_TO_SELECT_CLASS": d.get("EVENT_COUNT_USED_TO_SELECT_CLASS"),
        "COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": d.get("COMPOSITE_ARCHITECTURE_EVENT_COUNT_N"),
        "NEW_TRIGGER_DEFINITION_N": d.get("NEW_TRIGGER_DEFINITION_N"),
        "NEW_THRESHOLD_DEFINITION_N": d.get("NEW_THRESHOLD_DEFINITION_N"),
        "CANDIDATE_LIBRARY_GENERATED": d.get("CANDIDATE_LIBRARY_GENERATED"),
        "C4_ALSO_ELIGIBLE": C4_CLASS in eligible,
        "WHY_C1_FIRST": (
            "C1 is an unexecuted mixed-timeframe ENTRY information structure. "
            "C4 is admission/crowding on an existing candidate stream. "
            "Precommitted class priority processes C1 first. PnL unused."
        ),
    }
