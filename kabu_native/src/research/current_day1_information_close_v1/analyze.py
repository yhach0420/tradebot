"""Read-only close archive. No new Day1 mining. No Day2 freeze rewrite."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.current_day1_information_close_v1 import (
    ANALYSIS_ID,
    DAY2_PRIMARY,
    DAY2_PRIMARY_ID,
    FEATURE_MINING_CLOSED,
    KIND,
    NEXT,
    NEXT_SOURCE,
    PARENT_ID,
    RECON_Q1,
    RECON_Q2,
    STOP_FORBIDDEN,
    TRADING_DATE,
    VERDICT,
)
from research.current_day1_information_close_v1.isolation import NATIVE
from research.day2_futures_plus_market_breadth_acquisition_v1 import (
    BREADTH_LIVE_COMMAND,
    CASE_LIVE_READY,
    FUTURES_LIVE_COMMAND,
)
from research.market_breadth_leadership_acquisition_v1 import (
    DEFAULT_CADENCE_SEC,
    FALLBACK_CADENCE_SEC,
    PRIMARY_WINDOW_END_HM,
    PRIMARY_WINDOW_START_HM,
    RANKING_TYPES,
)
from research.market_breadth_leadership_acquisition_v1.derive import (
    FORBIDDEN_PRIMARY_KEYS,
    PRIMARY_CONTEXT_CANDIDATES,
)
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")

PARENT_CHAIN = (
    ("futures_context_day1_effect_check_v1", "futures-only price lead"),
    ("futures_context_day1_lead_lag_rca_v1", "lead-lag RCA"),
    ("futures_microstructure_pressure_day1_v1", "futures book pressure"),
    ("futures_x_stock_state_interaction_day1_v1", "futures × stock activity"),
    ("futures_x_stock_state_day1_candidate_mechanism_audit_v1", "T-time BOTH_DOWN audit"),
    ("futures_reversal_precursor_day1_v1", "reversal precursor"),
    ("futures_reversal_confirmed_entry_day1_v1", "actual BOTH_UP confirmation"),
    ("futures_reversal_x_cash_participation_day1_v1", "cash participation confirmation"),
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"missing parent artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _parent_decision(native_root: Path, folder: str, label: str) -> dict[str, Any]:
    path = native_root / "results" / "research" / folder / "report.json"
    body = _read_json(path)
    d = dict(body.get("decision") or {})
    a = dict(body.get("answers") or {})
    return {
        "folder": folder,
        "label": label,
        "path": str(path),
        "CASE": d.get("CASE") or a.get("35_CASE") or a.get("CASE"),
        "TYPE": d.get("TYPE"),
        "VERDICT": d.get("VERDICT") or a.get("36_VERDICT") or a.get("33_VERDICT") or a.get("34_VERDICT"),
        "NEXT": d.get("NEXT") or a.get("37_NEXT") or a.get("34_NEXT"),
    }


def _load_freeze(native_root: Path) -> dict[str, Any]:
    path = Path(native_root) / "results" / "research" / "futures_x_stock_state_day1_candidate_mechanism_audit_v1" / "day2_freeze_manifest.json"
    man = _read_json(path)
    primary = dict(man.get("primary") or {})
    if str(man.get("analysis_id")) != DAY2_PRIMARY_ID:
        raise RuntimeError("Day2 freeze analysis_id drifted")
    if man.get("substitutions_allowed") is not False:
        raise RuntimeError("Day2 substitutions_allowed drifted")
    if primary.get("selector") != "OBSERVED_TRADE_N_180S":
        raise RuntimeError("Day2 selector drifted")
    if primary.get("horizon") != "10m":
        raise RuntimeError("Day2 horizon drifted")
    if primary.get("direction") != "LONG TOP16":
        raise RuntimeError("Day2 direction drifted")
    if primary.get("context") != "AGREEMENT_180S_BOTH_DOWN":
        raise RuntimeError("Day2 context drifted")
    return {
        "path": str(path),
        "analysis_id": man.get("analysis_id"),
        "primary": primary,
        "substitutions_allowed": man.get("substitutions_allowed"),
        "ten_m_is_diagnostic_not_exit": man.get("ten_m_is_diagnostic_not_exit"),
        "entry_built": man.get("entry_built"),
        "exit_built": man.get("exit_built"),
        "day1_verdict": man.get("day1_verdict"),
        "rewritten_by_this_close": False,
    }


def run_close(*, native_root: Optional[Path] = None) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    parents = [_parent_decision(root, folder, label) for folder, label in PARENT_CHAIN]
    freeze = _load_freeze(root)
    cash = _read_json(root / "results" / "research" / "futures_reversal_x_cash_participation_day1_v1" / "report.json")
    cash_a = dict(cash.get("answers") or {})
    winner = cash_a.get("7_09:21:46_CASH_states")
    loser = cash_a.get("8_10:32:39_CASH_states")
    orders = live_order_counts()
    answers = {
        "1_Day1_existing_information_exhausted": True,
        "2_futures_only_price_lead_usable": False,
        "3_futures_book_lead_usable": False,
        "4_futures_x_activity_interaction_exists": "relative yes",
        "5_T_time_BOTH_DOWN_sufficient": False,
        "6_precursor_sufficient": False,
        "7_actual_BOTH_UP_sufficient": False,
        "8_cash_participation_sufficient": False,
        "9_causal_ENTRY_identified": False,
        "10_EXIT_identified": False,
        "11_Day1_feature_mining_closed": True,
        "12_Day2_primary_unchanged": True,
        "13_substitutions_allowed": False,
        "14_next_information_source": NEXT_SOURCE,
        "15_Runtime_changed": False,
        "16_Paper_changed": False,
        "17_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "18_VERDICT": VERDICT,
        "19_NEXT": NEXT,
    }
    decision = {
        "VERDICT": VERDICT,
        "NEXT": NEXT,
        "FEATURE_MINING_CLOSED": FEATURE_MINING_CLOSED,
        "ENTRY_built": False,
        "EXIT_built": False,
        "Day2_primary_changed": False,
        "Day2_substitutions_allowed": False,
        "day2_primary": dict(DAY2_PRIMARY),
        "day2_primary_id": DAY2_PRIMARY_ID,
        "day2_primary_purpose": "Does the Day1 cross-sectional interaction repeat exactly? Not ENTRY certification.",
        "next_source": NEXT_SOURCE,
        "next_live_ready": CASE_LIVE_READY,
        "submit_cancel_live": answers["17_submit_cancel_live"],
    }
    return {
        "analysis_id": ANALYSIS_ID,
        "parent_id": PARENT_ID,
        "kind": KIND,
        "trading_date_closed": TRADING_DATE,
        "built_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "parents": parents,
        "binding_counterexample": {
            "winner": winner,
            "loser": loser,
            "interpretation": (
                "cash-confirmed reversal does NOT identify executable winner state"
            ),
        },
        "scientific_finding_retained": (
            "During some risk-off / reversal states, high-activity stocks can "
            "outperform low-activity stocks cross-sectionally. This is not ENTRY."
        ),
        "stop_forbidden": list(STOP_FORBIDDEN),
        "day2_freeze": freeze,
        "next_tape": {
            "source": NEXT_SOURCE,
            "live_ready": CASE_LIVE_READY,
            "endpoint": "/ranking",
            "types": list(RANKING_TYPES),
            "window_jst": f"{PRIMARY_WINDOW_START_HM[0]:02d}:{PRIMARY_WINDOW_START_HM[1]:02d}–{PRIMARY_WINDOW_END_HM[0]:02d}:{PRIMARY_WINDOW_END_HM[1]:02d}",
            "cadence_sec": DEFAULT_CADENCE_SEC,
            "fail_soft_cadence_sec": FALLBACK_CADENCE_SEC,
            "register_unregister_mutation": False,
            "futures_live_command": FUTURES_LIVE_COMMAND,
            "breadth_live_command": BREADTH_LIVE_COMMAND,
            "predefined_metrics": list(PRIMARY_CONTEXT_CANDIDATES) + ["SECTOR.RANK_PERSISTENCE"],
            "forbidden_raw_primaries": list(FORBIDDEN_PRIMARY_KEYS),
            "first_questions": [RECON_Q1, RECON_Q2],
            "large_futures_breadth_stock_grid": False,
            "do_not_wait_10_days": True,
            "same_day_recon_after_transport_full": True,
        },
        "answers": answers,
        "decision": decision,
    }
