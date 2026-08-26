"""Current ENTRY binding audit. Fails closed if P2-1 SHA or live path is missing."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any

from research.dynamic_anchor_p2_1.publish import CONFIRM_SHA_KEYS, TRIGGER_SHA_KEYS, ledger_sha
from research.dynamic_anchor_p2_2 import P2_1_CONFIRM_SHA, P2_1_TRIGGER_SHA
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x36_joint_allocator.replay import simulate_joint
from small_paper.v1r_native_entry_live import FEATURE_ORDER, V1RNativeEntryLive
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

NATIVE = Path(__file__).resolve().parents[3]
P2_1_REPORT = NATIVE / "results" / "research" / "dynamic_anchor_event_validation_p2_1" / "report.json"
P1_REPORT = NATIVE / "results" / "research" / "current_runtime_full_capture_recalc_p1" / "report.json"


ENTRY_BINDING = {
    "T1_C1": "research.dynamic_anchor_p2_1.inventory.process_day (frozen P2-1 causal grid; same Capture)",
    "preentry_feature_creation": "research.e1_x34b_entry_execution.features.preentry_from_board",
    "FEATURE_ORDER": list(FEATURE_ORDER),
    "score_calculation": "V1RNativeEntryLive.score_fn (score_fn_from_serialized / model artifact)",
    "candidate_ranking": "research.e1_x36_joint_allocator.replay.simulate_joint — higher alloc_score, tie symbol ASC",
    "candidate_selection": "simulate_joint admit until POSITION_CAP, then live exposure/pending/open checks in V1RNativeEntryLive._run_anchor",
    "rank_pass_gate": None,
    "eligibility": "finite FEATURE_ORDER + finite score + bid>0 at last board t<=t1",
    "same_symbol": "V1RNativeEntryLive: symbol in pending or open_symbols → skip",
    "PENDING": "PendingOrder + on_tick_fill_check",
    "POSITION_CAP": POSITION_CAP,
    "admission": "V1RNativeEntryLive._run_anchor → V1R_ENTRY_PENDING",
    "limit_price": "last bid at board.t <= snapshot_cutoff (t1)",
    "Passive_Fill": "research.e1_x34a_execution_policy.arms.find_ask_cross_fill WAIT_SEC=" + str(WAIT_SEC),
    "EXIT": "V1RLiveDualLane.on_tick Arch E 600/750 + IMBALANCE + SESSION_CLOSE",
    "signal_time": "t1",
    "snapshot_cutoff": "t1 (preentry_from_board searchsorted side=right, last t<=t1)",
    "decision_time": "first global market event with event_t > t1",
    "fixed_clock_disabled": "DynamicEngine.maybe_fire_anchor returns []",
}


def _file_sha(rel: str) -> str:
    p = NATIVE / rel
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_p2_1_shas(report: dict[str, Any]) -> dict[str, Any]:
    t1 = str(report.get("TRIGGER_LEDGER_SHA_RUN1") or "")
    t2 = str(report.get("TRIGGER_LEDGER_SHA_RUN2") or "")
    c1 = str(report.get("CONFIRM_LEDGER_SHA_RUN1") or "")
    c2 = str(report.get("CONFIRM_LEDGER_SHA_RUN2") or "")
    trig_ok = t1 == P2_1_TRIGGER_SHA and t2 == P2_1_TRIGGER_SHA
    conf_ok = c1 == P2_1_CONFIRM_SHA and c2 == P2_1_CONFIRM_SHA
    return {
        "P2_1_TRIGGER_SHA_MATCH": "PASS" if trig_ok else "FAIL",
        "P2_1_CONFIRM_SHA_MATCH": "PASS" if conf_ok else "FAIL",
        "observed_trigger_run1": t1,
        "observed_trigger_run2": t2,
        "observed_confirm_run1": c1,
        "observed_confirm_run2": c2,
        "expected_trigger": P2_1_TRIGGER_SHA,
        "expected_confirm": P2_1_CONFIRM_SHA,
        "pass": bool(trig_ok and conf_ok),
    }


def verify_entry_binding() -> dict[str, Any]:
    """Confirm we can import and call the live functions — do not substitute."""
    missing = []
    if not inspect.isfunction(preentry_from_board):
        missing.append("preentry_from_board")
    if not inspect.isfunction(simulate_joint):
        missing.append("simulate_joint")
    if not inspect.isfunction(find_ask_cross_fill):
        missing.append("find_ask_cross_fill")
    if not hasattr(V1RNativeEntryLive, "_run_anchor"):
        missing.append("V1RNativeEntryLive._run_anchor")
    if not hasattr(V1RNativeEntryLive, "on_tick_fill_check"):
        missing.append("V1RNativeEntryLive.on_tick_fill_check")
    if list(FEATURE_ORDER) != [
        "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"
    ]:
        missing.append("FEATURE_ORDER")
    if int(POSITION_CAP) != 5:
        missing.append("POSITION_CAP")
    if float(WAIT_SEC) != 1.0:
        missing.append("WAIT_SEC")
    return {
        "CURRENT_ENTRY_BINDING": "PASS" if not missing else "FAIL",
        "missing": missing,
        "path": ENTRY_BINDING,
        "V1RNativeEntryLive_sha": _file_sha("src/small_paper/v1r_native_entry_live.py"),
        "V1RLiveDualLane_sha": _file_sha("src/small_paper/v1r_live_dual_lane.py"),
    }


def instream_ledger_sha(triggers: list[dict[str, Any]], confirms: list[dict[str, Any]]) -> tuple[str, str]:
    return ledger_sha(triggers, TRIGGER_SHA_KEYS), ledger_sha(confirms, CONFIRM_SHA_KEYS)
