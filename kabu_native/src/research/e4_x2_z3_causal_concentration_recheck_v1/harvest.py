"""Read-only load of SIMPLE_FULL_STRATEGY_DISCOVERY_V1 DEV cache. No Capture stream. Stress sealed."""
from __future__ import annotations

import json
from typing import Any

from research.simple_full_strategy_discovery_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.simple_full_strategy_discovery_v1.spec import candidate_ids
from research.e4_x2_z3_causal_concentration_recheck_v1.isolation import PRIOR_CACHE

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "STRESS_METRIC_COMPUTE_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "SPLIT_LEAKAGE_N": 0,
    "EXTRA_CANDIDATE_N": 0,
    "PRIOR_CACHE_WRITE_N": 0,
}


def assert_dev_only_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        AUDIT["STRESS_FILE_OPEN_N"] += 1
        AUDIT["STRESS_METRIC_COMPUTE_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_BURNED_READ_N"] += 1
        raise RuntimeError("HOLDOUT_BURNED_READ")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE:{d}")
    if d not in DEVELOPMENT_DAYS:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"NON_DEV:{d}")


def load_prior_development_grid() -> dict[str, Any]:
    ids = candidate_ids()
    if len(ids) != 75:
        AUDIT["EXTRA_CANDIDATE_N"] += 1
        return {"ok": False, "blocker": "CANDIDATE_N"}
    allowed = set(ids)
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in ids}
    ctrl_by: dict[str, list[dict[str, Any]]] = {}
    day_ok: dict[str, bool] = {}
    for day in DEVELOPMENT_DAYS:
        try:
            assert_dev_only_day(str(day))
        except RuntimeError as exc:
            return {"ok": False, "blocker": str(exc), "day_ok": day_ok}
        path = PRIOR_CACHE / f"DEVELOPMENT_{day}_grid.json"
        if not path.is_file():
            return {"ok": False, "blocker": f"PRIOR_CACHE_MISSING:{day}", "day_ok": day_ok}
        body = json.loads(path.read_text(encoding="utf-8"))
        if not body.get("ok") or str(body.get("date") or "") != str(day):
            return {"ok": False, "blocker": f"PRIOR_CACHE_BAD:{day}", "day_ok": day_ok}
        extra = [c for c in (body.get("rows_by") or {}) if c not in allowed]
        if extra:
            AUDIT["EXTRA_CANDIDATE_N"] += len(extra)
            return {"ok": False, "blocker": "EXTRA_CANDIDATE", "day_ok": day_ok}
        for c, xs in (body.get("rows_by") or {}).items():
            for r in xs or []:
                rd = str(r.get("date") or "")
                if rd != str(day):
                    AUDIT["SPLIT_LEAKAGE_N"] += 1
                if rd in BURNED_HOLDOUT_DAYS:
                    AUDIT["HOLDOUT_BURNED_READ_N"] += 1
                    return {"ok": False, "blocker": "HOLDOUT_IN_CACHE", "day_ok": day_ok}
                if rd in STRESS_DAYS:
                    AUDIT["STRESS_READ_N"] += 1
                    return {"ok": False, "blocker": "STRESS_IN_CACHE", "day_ok": day_ok}
            rows_by.setdefault(c, []).extend(list(xs or []))
        for c, xs in (body.get("ctrl_by") or {}).items():
            ctrl_by.setdefault(c, []).extend(list(xs or []))
        day_ok[str(day)] = True
        print(f"cache-read DEVELOPMENT {day}", flush=True)
    return {"ok": True, "rows_by": rows_by, "ctrl_by": ctrl_by, "day_ok": day_ok, "audit": dict(AUDIT)}
