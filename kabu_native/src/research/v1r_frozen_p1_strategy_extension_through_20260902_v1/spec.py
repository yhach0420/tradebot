"""V1R frozen P1 strategy extension through 20260902. Research only. No Sep-05 Runtime replay."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS

ANALYSIS_ID = "V1R_FROZEN_P1_STRATEGY_EXTENSION_THROUGH_20260902_V1"
EVALUATION_TARGET = "FROZEN_P1_V1R_STRATEGY"
HISTORICAL_P1_RESULT_NAME = "CURRENT_RUNTIME_REPLAY"
HISTORICAL_P1_RESULT_NAME_MEANS = "Runtime as of 2026-08-21, not 2026-09-05 working tree"
STRATEGY_NAME = "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY"

MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
FIRST_PROSPECTIVE_DAY = "20260907"
PROSPECTIVE_HARVEST_SUSPENDED = True
FUTURE_DATA_USED = False
TRUE_OOS = False
CERTIFIED = False

EXTENSION_CANDIDATE_DAYS = (
    "20260824",
    "20260825",
    "20260826",
    "20260827",
    "20260828",
    "20260831",
    "20260901",
    "20260902",
)
SPOT_PREFERENCE = ("20260828", "20260902")
STRESS4_DAYS = ("20260828", "20260831", "20260901", "20260902")
MIN_EXTENSION_FULL_DAY_N = 6
MIN_EXTENSION_FULL_TRADE_N = 80

P1_PERIOD = "20260721 - 20260821"
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
P1_FULL_TRADES = 267
P1_FULL_PNL = 2289100.0
P1_FULL_PF = 3.5711557901830844
P1_FULL_MAXDD = -210300.0
P1_FULL_WIN = 97
P1_FULL_LOSS = 80
P1_FULL_DRAW = 90
P1_FULL_POS_DAYS = 9
P1_FULL_NEG_DAYS = 5
P1_FULL_FLAT_DAYS = 0
P1_FULL_AM_TRADES = 163
P1_FULL_AM_PNL = 2048500.0
P1_FULL_PM_TRADES = 104
P1_FULL_PM_PNL = 240600.0
P1_REF_DAYS = 23
P1_REF_TRADES = 331
P1_REF_PNL = 2288850.0
P1_REF_PF = 3.098418519367408

STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
EXIT_SHA = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
ANCHOR_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
REPLAY_CODE_SHA = "510d304c0f081f628a9beeadc6fab1d785f0a6dd8be1c97ad801ccc6fa28e190"
V1R_NATIVE_ENTRY_LIVE_SHA = "e25285a7518d9c1b000dfdd15696a7518ab039ab1e0512932d1997b2467dc306"
V1R_LIVE_DUAL_LANE_SHA = "8107196739b0cb1ae96f8f9e4f82fa816d1b0fc1a7435074f2781defa70ac43d"
P1_RUNNER_SHA = "16d76a391ce30adfbf270a97764a64cb2ffaccaf92b00ea74bb46b317bbaad81"
ENTRY_V1R_SHA = "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a"
CONTROL_STRATEGY_SHA = "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a"

UNIVERSE_CONTRACT = "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1"
PINNED_FILES = (
    ("replay_code", "src/research/anchor_vs_event_driven/run_comparison.py", REPLAY_CODE_SHA),
    ("V1RNativeEntryLive", "src/small_paper/v1r_native_entry_live.py", V1R_NATIVE_ENTRY_LIVE_SHA),
    ("V1RLiveDualLane", "src/small_paper/v1r_live_dual_lane.py", V1R_LIVE_DUAL_LANE_SHA),
    ("p1_runner", "scripts/run_p1_current_runtime_full_capture_recalc.py", P1_RUNNER_SHA),
)

SIMPLE_TECH_CLOSED_VERDICT = "SIMPLE_TECH_ENTRY_FAMILY_ABSOLUTE_EDGE_EXHAUSTED"
SIMPLE_TECH_CASE = "C"
SIMPLE_TECH_DAYS = tuple(list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS))

SOURCE_FILES = (
    "spec.py",
    "isolation.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
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
            "EVALUATION_TARGET": EVALUATION_TARGET,
            "STRATEGY_NAME": STRATEGY_NAME,
            "STRATEGY_SHA": STRATEGY_SHA,
            "ENTRY_SHA": ENTRY_SHA,
            "EXIT_SHA": EXIT_SHA,
            "ANCHOR_SHA": ANCHOR_SHA,
            "REPLAY_CODE_SHA": REPLAY_CODE_SHA,
            "V1R_NATIVE_ENTRY_LIVE_SHA": V1R_NATIVE_ENTRY_LIVE_SHA,
            "V1R_LIVE_DUAL_LANE_SHA": V1R_LIVE_DUAL_LANE_SHA,
            "P1_RUNNER_SHA": P1_RUNNER_SHA,
            "UNIVERSE_CONTRACT": UNIVERSE_CONTRACT,
            "EXTENSION_CANDIDATE_DAYS": list(EXTENSION_CANDIDATE_DAYS),
            "P1_FULL14_DAYS": list(P1_FULL14_DAYS),
            "MIN_EXTENSION_FULL_DAY_N": MIN_EXTENSION_FULL_DAY_N,
            "MIN_EXTENSION_FULL_TRADE_N": MIN_EXTENSION_FULL_TRADE_N,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "FORBIDDEN_INPUT_DAYS": list(FORBIDDEN_INPUT_DAYS),
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "NEW_ENTRY_SEARCH": False,
            "NEW_EXIT_SEARCH": False,
            "SIMPLE_TECH_REOPEN": False,
            "SIZING_IMPLEMENTED": False,
            "SEP05_RUNTIME_REPLAY": False,
        }
    )


def spec_sha256() -> str:
    blob = json.dumps(canonical_spec(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
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
