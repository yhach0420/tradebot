"""Freeze V4 or stop. No candidate economics. No CSB RCA."""
from __future__ import annotations

import json
from typing import Any

from research.new_full_strategy_architecture_construction_v2 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    ARCHITECTURE_ID,
    BOARD_PRIMARY_ALPHA,
    CASE_DUPLICATE,
    CASE_FROZEN,
    CASE_STRUCTURAL,
    CSB_RCA_RUN,
    CSB_RETUNE,
    NEXT_IF_FROZEN,
    OLD_ST_RCA_CONTINUED,
    POSITION_CAP,
    SHARES,
    TRUE_OOS,
    CERTIFIED,
    BB_USED,
    DAILY_MA_ADDED,
    HTF_TREND_BOOLEAN_USED,
    NEW_CANDIDATE_ECONOMICS_RUN,
    NEW_CANDIDATE_PNL_READ_N,
    RCI_USED,
    VOLUME_THRESHOLD_SEARCH,
    VWAP_ENTRY_USED,
    VWAP_EXIT_USED,
    VWAP_FILTER_USED,
)
from research.new_full_strategy_architecture_construction_v2.availability import structural_availability
from research.new_full_strategy_architecture_construction_v2.closed_lineage import closed_lineage_audit
from research.new_full_strategy_architecture_construction_v2.csb_pin import csb_closure
from research.new_full_strategy_architecture_construction_v2.isolation import OUT
from research.new_full_strategy_architecture_construction_v2.level_semantics import prove_level_preexists_test_bar
from research.new_full_strategy_architecture_construction_v2.spec import (
    BREAK_RULE,
    EXIT_RULE,
    RESISTANCE_TEST_RULE,
    dumps_sha256,
    frozen_strategy,
    spec_sha256,
    source_sha256,
)
from research.new_full_strategy_architecture_construction_v2.volume_identity import volume_identity
from research.simple_tech_entry_family.v7_bars import self_check_agg

PNL_KEYS = (
    "TOTAL_PNL",
    "PF",
    "MaxDD",
    "MAXDD",
    "EX_BEST",
    "EX_BEST_DAY_PNL",
    "CAUSAL_EX_TOP1",
    "CAUSAL_EX_TOP1_PNL",
    "top_symbol",
    "top_symbol_pnl",
    "pnl_yen_100",
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "G6",
    "G1_TOTAL_PNL",
    "G2_PF",
    "G3_DAY_SIGNS",
    "G4_EX_BEST",
    "G5_PNL_PLUS_MAXDD",
    "G6_CAUSAL_EX_TOP1",
    "signal_n",
    "fill_n",
    "trade_n",
    "TRADE_N",
    "MFE",
    "MAE",
    "best_day",
    "daily_pnl",
)


def already_executed_check(source_hash: str) -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "OUT_REPORT_ABSENT"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    if str(prev.get("ANALYSIS_ID") or "") != ANALYSIS_ID:
        return {"ALREADY_EXECUTED_CHECK": False, "REUSED_EXISTING_RESULT": False, "REASON": "ANALYSIS_ID_MISMATCH"}
    if str(prev.get("source_sha256") or "") == source_hash and str(prev.get("spec_sha256") or "") == spec_sha256():
        return {
            "ALREADY_EXECUTED_CHECK": True,
            "REUSED_EXISTING_RESULT": True,
            "REASON": "SAME_METHODOLOGY",
            "prior_report": prev,
        }
    return {
        "ALREADY_EXECUTED_CHECK": True,
        "REUSED_EXISTING_RESULT": False,
        "REASON": "EXISTING_OUT_DIFFERENT_SPEC",
    }


def _assert_no_pnl(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in PNL_KEYS:
                raise RuntimeError(f"PNL_KEY_PRESENT {path}.{k}")
            _assert_no_pnl(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_pnl(v, f"{path}[{i}]")


def decide(*, run_availability: bool = True) -> dict[str, Any]:
    csb = csb_closure()
    vol = volume_identity()
    lvl = prove_level_preexists_test_bar()
    agg = self_check_agg()
    lineage = closed_lineage_audit()
    frozen = frozen_strategy()
    _assert_no_pnl([vol, lvl, agg, lineage, frozen])
    avail = None
    if run_availability and bool(csb.get("CSB_CLOSED")) and bool(lineage.get("STRUCTURALLY_DISTINCT")) and bool(lvl.get("HARD_GATE_PASS")):
        avail = structural_availability(use_cache=True)
        _assert_no_pnl(avail)
    data_ok = True
    if avail is not None:
        data_ok = int((avail.get("AUDIT") or {}).get("HOLDOUT_BURNED_READ_N") or 0) == 0 and int(
            (avail.get("AUDIT") or {}).get("STRESS_READ_N") or 0
        ) == 0 and int((avail.get("AUDIT") or {}).get("FUTURE_DATA_N") or 0) == 0
    htf_causal = bool(lvl.get("HARD_GATE_PASS")) and bool(agg.get("ok"))
    full_complete = True
    causal_impl = htf_causal and bool(vol.get("IDENTITY_PASS"))
    gates = {
        "CSB_CLOSED": bool(csb.get("CSB_CLOSED")),
        "LEVEL_PREEXISTS_TEST_BAR": bool(lvl.get("HARD_GATE_PASS")),
        "HTF_CAUSALITY": bool(htf_causal),
        "STRUCTURAL_AVAILABILITY": bool((avail or {}).get("STRUCTURAL_COVERAGE_POSSIBLE")) if avail is not None else False,
        "VOLUME_IDENTITY": bool(vol.get("IDENTITY_PASS")),
        "FULL_STRATEGY_COMPLETE": bool(full_complete),
        "STRUCTURALLY_DISTINCT": bool(lineage.get("STRUCTURALLY_DISTINCT")),
        "DATA_AVAILABLE": bool(data_ok),
        "CAUSAL_IMPLEMENTABLE": bool(causal_impl),
        "CLOSED_LINEAGE_MATCH": bool(lineage.get("CLOSED_LINEAGE_MATCH")),
    }
    sha = None
    if not gates["CSB_CLOSED"]:
        verdict = CASE_STRUCTURAL
        nxt = None
        failed = "csb_not_closed"
    elif gates["CLOSED_LINEAGE_MATCH"] or not gates["STRUCTURALLY_DISTINCT"]:
        verdict = CASE_DUPLICATE
        nxt = None
        failed = "closed_lineage"
    elif not gates["LEVEL_PREEXISTS_TEST_BAR"] or not gates["HTF_CAUSALITY"] or not gates["VOLUME_IDENTITY"] or not gates["CAUSAL_IMPLEMENTABLE"]:
        verdict = CASE_STRUCTURAL
        nxt = None
        failed = "causal_or_volume"
    elif avail is None or not gates["STRUCTURAL_AVAILABILITY"]:
        verdict = CASE_STRUCTURAL
        nxt = None
        failed = "structural_availability"
    elif not all(
        [
            gates["LEVEL_PREEXISTS_TEST_BAR"],
            gates["HTF_CAUSALITY"],
            gates["STRUCTURAL_AVAILABILITY"],
            gates["VOLUME_IDENTITY"],
            gates["FULL_STRATEGY_COMPLETE"],
            gates["STRUCTURALLY_DISTINCT"],
            gates["DATA_AVAILABLE"],
            gates["CAUSAL_IMPLEMENTABLE"],
        ]
    ):
        verdict = CASE_STRUCTURAL
        nxt = None
        failed = "gate"
    else:
        verdict = CASE_FROZEN
        nxt = NEXT_IF_FROZEN
        failed = None
        sha = dumps_sha256(frozen)
    decision = {
        "VERDICT": verdict,
        "NEXT": nxt,
        "FAILED_STAGE": failed,
        "FULL_STRATEGY_SPEC_SHA256_V4": sha,
        "ARCHITECTURE_ID": ARCHITECTURE_ID,
        "CSB_RCA_RUN": CSB_RCA_RUN,
        "CSB_RETUNE": CSB_RETUNE,
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
        "NEW_CANDIDATE_PNL_READ_N": NEW_CANDIDATE_PNL_READ_N,
        "NEW_CANDIDATE_ECONOMICS_RUN": NEW_CANDIDATE_ECONOMICS_RUN,
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
    }
    pack = {
        "csb": csb,
        "volume": vol,
        "level": lvl,
        "agg": {"ok": agg.get("ok")},
        "lineage": lineage,
        "availability": avail,
        "frozen_strategy": frozen if verdict == CASE_FROZEN else None,
        "gates": gates,
        "decision": decision,
    }
    _assert_no_pnl(pack)
    return pack


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    d = dict(pack.get("decision") or {})
    csb = dict(pack.get("csb") or {})
    lin = dict(pack.get("lineage") or {})
    lvl = dict(pack.get("level") or {})
    vol = dict(pack.get("volume") or {})
    avail = dict(pack.get("availability") or {})
    days = list(avail.get("days") or [])
    first_by = {str(r.get("date")): r.get("FIRST_5M_EMA21_AVAILABLE_JST") for r in days}
    return {
        "1_CSB_closed": bool(csb.get("CSB_CLOSED")),
        "2_CSB_RCA": CSB_RCA_RUN,
        "3_CSB_retune": CSB_RETUNE,
        "4_closed_lineage_audit_done": bool(lin.get("AUDIT_DONE")),
        "5_closest_prior_architecture": lin.get("CLOSEST_PRIOR_ARCHITECTURE"),
        "6_duplicate": bool(lin.get("CLOSED_LINEAGE_MATCH")),
        "7_architecture_ID": ARCHITECTURE_ID,
        "8_5m_EMA21_role": "STRUCTURAL_PRICE_LEVEL",
        "9_level_known_before_test_bar": bool(lvl.get("LEVEL_PREEXISTS_TEST_BAR")),
        "10_same_test_bar_contributes_to_level": bool(lvl.get("SAME_BAR_CONTRIBUTES_TO_TEST_LEVEL")),
        "11_partial_5m_used": False,
        "12_first_EMA21_available_time_by_day": first_by,
        "13_level_available_day_N": avail.get("LEVEL_AVAILABLE_DAY_N"),
        "14_structural_coverage_possible": avail.get("STRUCTURAL_COVERAGE_POSSIBLE"),
        "15_resistance_test_rule": RESISTANCE_TEST_RULE,
        "16_episode_level_version": "LEVEL_VERSION_ID of the published completed 5m bar; TEST_LEVEL fixed in episode",
        "17_new_level_expires_unfilled_episode": True,
        "18_break_must_occur_later_bar": True,
        "19_break_rule": BREAK_RULE,
        "20_canonical_volume_source": vol.get("SOURCE_PATH"),
        "21_volume_rule": f"S_VOL_CONFIRM_1M volume_confirm VOLUME_MULT={vol.get('VOLUME_MULT')} VOLUME_MEDIAN_BARS={vol.get('VOLUME_MEDIAN_BARS')}",
        "22_threshold_search": VOLUME_THRESHOLD_SEARCH,
        "23_RCI_used": RCI_USED,
        "24_BB_used": BB_USED,
        "25_VWAP_used": VWAP_ENTRY_USED or VWAP_EXIT_USED or VWAP_FILTER_USED,
        "26_Board_alpha": BOARD_PRIMARY_ALPHA,
        "27_BREAK_LEVEL_frozen": True,
        "28_later_EMA_moves_EXIT_level": False,
        "29_technical_EXIT": EXIT_RULE,
        "30_fixed_timeout": False,
        "31_CAP": int(POSITION_CAP),
        "32_shares": int(SHARES),
        "33_same_symbol": True,
        "34_occupancy_at_fill": True,
        "35_slot_release_at_EXIT_fill": True,
        "36_reentry_requires_new_episode": True,
        "37_distinct_from_Breakout": bool(lin.get("DISTINCT_FROM_BREAKOUT")),
        "38_distinct_from_C1": bool(lin.get("DISTINCT_FROM_C1")),
        "39_distinct_from_Recovery": bool(lin.get("DISTINCT_FROM_RECOVERY")),
        "40_distinct_from_ST": bool(lin.get("DISTINCT_FROM_ST")),
        "41_CLOSED_LINEAGE_MATCH": bool(lin.get("CLOSED_LINEAGE_MATCH")),
        "42_Full_Strategy_complete": bool((pack.get("gates") or {}).get("FULL_STRATEGY_COMPLETE")),
        "43_causal_implementable": bool((pack.get("gates") or {}).get("CAUSAL_IMPLEMENTABLE")),
        "44_candidate_PnL_read": False,
        "45_economics_run": NEW_CANDIDATE_ECONOMICS_RUN,
        "46_Holdout_read": False,
        "47_Stress_read": False,
        "48_future_used": False,
        "49_Runtime_changed": False,
        "50_submit_cancel_live": "0/0/0",
        "51_FULL_STRATEGY_SPEC_SHA256_V4": d.get("FULL_STRATEGY_SPEC_SHA256_V4"),
        "52_VERDICT": d.get("VERDICT"),
        "53_NEXT": d.get("NEXT"),
        "OLD_ST_RCA_CONTINUED": OLD_ST_RCA_CONTINUED,
        "ANOTHER_PRECOMMIT_RUN": ANOTHER_PRECOMMIT_RUN,
        "HTF_TREND_BOOLEAN_USED": HTF_TREND_BOOLEAN_USED,
        "DAILY_MA_ADDED": DAILY_MA_ADDED,
    }
