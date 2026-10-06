"""P1 V1R source forensic recovery. Research only. No main-tree restore. No economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ANALYSIS_ID = "V1R_P1_SOURCE_FORENSIC_RECOVERY_V1"
PRIOR_ANALYSIS_ID = "V1R_FROZEN_P1_STRATEGY_EXTENSION_THROUGH_20260902_V1"
PRIOR_VERDICT = "V1R_FROZEN_REPLAY_INTEGRITY_FAILED"
PRIOR_CASE = "E"

NATIVE_REL = "src/small_paper/v1r_native_entry_live.py"
DUAL_REL = "src/small_paper/v1r_live_dual_lane.py"
EXPECTED_NATIVE_SHA = "e25285a7518d9c1b000dfdd15696a7518ab039ab1e0512932d1997b2467dc306"
EXPECTED_DUAL_SHA = "8107196739b0cb1ae96f8f9e4f82fa816d1b0fc1a7435074f2781defa70ac43d"

P1_FULL14_DAYS = (
    "20260722",
    "20260728",
    "20260729",
    "20260730",
    "20260731",
    "20260803",
    "20260804",
    "20260805",
    "20260806",
    "20260807",
    "20260810",
    "20260817",
    "20260819",
    "20260820",
)

MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
PROSPECTIVE_HARVEST_SUSPENDED = True
FUTURE_DATA_USED = False

SOURCE_FILES = (
    "spec.py",
    "isolation.py",
    "harvest.py",
    "calib.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)

UNIQUE_IDS = (
    "class V1RNativeEntryLive",
    "class V1RLiveDualLane",
    "PASSIVE_FILL_ENTRY_V1",
    "V1R_ENTRY_PENDING",
    "on_tick_fill_check",
    "CONT_EXIT_600",
    "CONT_EXTEND_750",
)

NATIVE_REPLAY_FUNCS = (
    "__init__",
    "ingest_push",
    "extract_board_row",
    "maybe_fire_anchor",
    "_run_anchor",
    "on_tick_fill_check",
    "_promote_fill",
)
DUAL_REPLAY_FUNCS = (
    "register",
    "on_tick",
    "admit_v1r_fill",
    "session_end",
)


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if obj is None:
        return None
    return str(obj)


def canonical_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "EXPECTED_NATIVE_SHA": EXPECTED_NATIVE_SHA,
            "EXPECTED_DUAL_SHA": EXPECTED_DUAL_SHA,
            "NATIVE_REL": NATIVE_REL,
            "DUAL_REL": DUAL_REL,
            "P1_FULL14_DAYS": list(P1_FULL14_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "FORBIDDEN_INPUT_DAYS": list(FORBIDDEN_INPUT_DAYS),
            "MAIN_TREE_CHECKOUT": False,
            "MAIN_SRC_RESTORE": False,
            "RUNTIME_REWRITE": False,
            "EXTENSION_ECONOMICS": False,
            "NEW_ENTRY": False,
            "NEW_EXIT": False,
            "SIZING": False,
        }
    )


def spec_sha256() -> str:
    blob = json.dumps(canonical_spec(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()
