"""Read the occupied Kabu register. Do not PUT, clear, or drop names."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.kabu_register import KABU_PUSH_REGISTER_LIMIT, normalize_symbol_code
from research.causal_driver_pb1 import PROSPECTIVE_FROM
from research.causal_driver_pb1.sector_state_alpha_shadow.isolation import NATIVE
from research.new_causal_information_acquisition_v1 import CORE_N, DYNAMIC_N, FUTURES_N, TOTAL_REGISTRATION_N
from research.new_causal_information_acquisition_v1.register_plan import REGISTER_LIMIT


def _register_files() -> list[tuple[str, Path]]:
    root = NATIVE / "data" / "market_context_capture"
    found: list[tuple[str, Path]] = []
    if not root.is_dir():
        return found
    for path in root.glob("*/station_state/runtime/paper_register_state.json"):
        day = path.parents[2].name
        if day >= PROSPECTIVE_FROM:
            raise RuntimeError("prospective_register_state_refused")
        found.append((day, path))
    return sorted(found)


def audit_coexistence(m3_symbols: list[str]) -> dict[str, Any]:
    files = _register_files()
    if not files:
        return {
            "pass": False,
            "status": "SHADOW_LIVE_REGISTRATION_CONFLICT",
            "reason": "NO_CURRENT_REGISTER_STATE",
            "existing_mandatory_register_n": None,
            "m3_required_n": len(m3_symbols),
            "overlap_n": None,
            "union_n": None,
            "coexistence_possible": False,
            "live_registration_changed": False,
            "register_limit": int(KABU_PUSH_REGISTER_LIMIT),
        }
    day, path = files[-1]
    doc = json.loads(path.read_text(encoding="utf-8"))
    if str(doc.get("trading_date") or day) >= PROSPECTIVE_FROM:
        raise RuntimeError("prospective_register_state_refused")
    existing = [normalize_symbol_code(s) for s in (doc.get("symbol_codes") or [])]
    m3 = [normalize_symbol_code(s) for s in m3_symbols]
    existing_set = set(existing)
    m3_set = set(m3)
    overlap = sorted(existing_set & m3_set)
    union = sorted(existing_set | m3_set)
    limit = int(KABU_PUSH_REGISTER_LIMIT)
    possible = len(union) <= limit
    return {
        "pass": possible,
        "status": "COEXISTENCE_PLAN_FEASIBLE" if possible else "SHADOW_LIVE_REGISTRATION_CONFLICT",
        "reason": "" if possible else "UNION_EXCEEDS_REGISTER_LIMIT",
        "source_day": day,
        "source_path": str(path).replace("\\", "/"),
        "planner_contract": {
            "core_n": int(CORE_N),
            "dynamic_n": int(DYNAMIC_N),
            "futures_n": int(FUTURES_N),
            "total_registration_n": int(TOTAL_REGISTRATION_N),
            "register_plan_limit": int(REGISTER_LIMIT),
        },
        "existing_symbols": existing,
        "existing_mandatory_register_n": len(existing_set),
        "m3_required_n": len(m3_set),
        "overlap_n": len(overlap),
        "overlap_symbols": overlap,
        "union_n": len(union),
        "union_symbols": union,
        "coexistence_possible": possible,
        "names_dropped": 0,
        "live_registration_changed": False,
        "register_limit": limit,
    }
