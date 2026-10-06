"""Bracketed A2 complete-strategy economics. Development gates. No PnL rule change."""
from __future__ import annotations

from statistics import median
from typing import Any

from research.sr_a2_bracketed_structure_complete_strategy_v1 import (
    CASE_FAIL,
    CASE_PASS_CONTEXT,
    CASE_PASS_STRUCTURAL,
    NEXT_CONFIRM,
    NEXT_STOP,
    PARENT_VERDICT,
    POST_HOC_DEVELOPMENT_ARCHITECTURE,
    SHARES,
    STRATEGY_ID,
    STRATEGY_SPEC,
    X1_STRESS_BPS,
)
from research.sr_a2_bracketed_structure_complete_strategy_v1.adjust import overlap_available
from research.sr_a2_bracketed_structure_complete_strategy_v1.confound import confound_audit, path_split
from research.sr_a2_bracketed_structure_complete_strategy_v1.walk import walk_bracketed
from research.support_resistance_mechanism_to_complete_strategy_v1 import CAP
from research.support_resistance_mechanism_to_complete_strategy_v1.metrics import summarize
from research.support_resistance_mechanism_to_complete_strategy_v1.portfolio import replay


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _bps_overlay(trades: list[dict[str, Any]], econ: dict[str, Any]) -> dict[str, Any]:
    b0 = [float(t["realized_bps"]) for t in trades if _finite(t.get("realized_bps"))]
    b1 = [float(x) - float(X1_STRESS_BPS) for x in b0]
    mfe = [float(t["mfe_bps"]) for t in trades if _finite(t.get("mfe_bps"))]
    gb = [float(t["mfe_realized_giveback_bps"]) for t in trades if _finite(t.get("mfe_realized_giveback_bps"))]
    holds = [int(t.get("hold_min") or 0) for t in trades]
    dist = [float(t["target_distance_bps"]) for t in trades if _finite(t.get("target_distance_bps"))]
    n = len(trades)
    tgt = int(econ.get("target_exit_n") or 0)
    econ.update(
        {
            "mean_gross_bps": (sum(b0) / len(b0)) if b0 else None,
            "median_gross_bps": float(median(b0)) if b0 else None,
            "mean_stress_bps": (sum(b1) / len(b1)) if b1 else None,
            "median_stress_bps": float(median(b1)) if b1 else None,
            "mean_mfe_bps": (sum(mfe) / len(mfe)) if mfe else None,
            "median_mfe_bps": float(median(mfe)) if mfe else None,
            "mean_giveback_bps": (sum(gb) / len(gb)) if gb else None,
            "median_giveback_bps": float(median(gb)) if gb else None,
            "mean_hold": (sum(holds) / len(holds)) if holds else None,
            "median_hold": float(median(holds)) if holds else None,
            "target_available_n": n,
            "target_reached_n": tgt,
            "target_exit_rate": (tgt / n) if n else None,
            "mean_target_distance_bps": (sum(dist) / len(dist)) if dist else None,
            "median_target_distance_bps": float(median(dist)) if dist else None,
        }
    )
    return econ


def _block_ok(blocks: dict[str, Any], name: str) -> bool:
    b = dict(blocks.get(name) or {})
    st = b.get("stress_yen")
    pf = b.get("pf_stress")
    return b.get("n", 0) > 0 and st is not None and float(st) > 0 and pf is not None and float(pf) > 1


def _run(signals: list[dict[str, Any]], *, order_name: str) -> dict[str, Any]:
    primary = replay(signals, strategy_id=STRATEGY_ID, cap=CAP, order_name=order_name)
    econ = summarize(primary["trades"], label=f"{STRATEGY_ID}:{order_name}")
    econ = _bps_overlay(primary["trades"], econ)
    econ["signal_n"] = primary["signal_n"]
    econ["entry_attempts"] = primary["entry_attempts"]
    econ["filled_historical_entries"] = primary["filled_n"]
    econ["cap_blocked_n"] = primary["cap_blocked_n"]
    econ["same_symbol_blocked_n"] = primary["same_symbol_blocked_n"]
    econ["reentry_n"] = primary["reentry_n"]
    econ["occupancy_mean_at_entry"] = primary["occupancy_mean_at_entry"]
    days = {str(t.get("date")) for t in primary["trades"]}
    econ["slot_utilization"] = (len(primary["trades"]) / (CAP * len(days))) if days else 0.0
    return {**primary, "economics": econ}


def decide(
    *,
    walked: dict[str, Any],
    primary: dict[str, Any],
    reverse: dict[str, Any],
    hashed: dict[str, Any],
    confound: dict[str, Any],
) -> dict[str, Any]:
    e = dict(primary.get("economics") or {})
    blocks = dict(e.get("blocks") or {})
    conc = dict(e.get("concentration") or {})
    gates = {
        "eligibility_before_entry": int(walked.get("TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N") or 0) == 0
        and int(walked.get("TARGET_ELIGIBILITY_FUTURE_BAR_N") or 0) == 0,
        "same_bar_entry_0": int(walked.get("same_bar_entry_n") or 0) == 0,
        "future_leakage_0": int(walked.get("target_future_leakage_n") or 0) == 0,
        "D2": _block_ok(blocks, "D2"),
        "D3": _block_ok(blocks, "D3"),
        "D4": _block_ok(blocks, "D4"),
        "overall_stress": float(e.get("stress_yen") or 0) > 0,
        "overall_pf_stress": e.get("pf_stress") is not None and float(e.get("pf_stress") or 0) > 1,
        "effect_in_bps": e.get("mean_stress_bps") is not None and float(e.get("mean_stress_bps") or 0) > 0,
        "not_notional_only": not bool(confound.get("notional_confound")),
        "tie_all_positive_stress": all(
            float((x.get("economics") or {}).get("stress_yen") or 0) > 0 for x in (primary, reverse, hashed)
        ),
        "not_one_symbol": float((conc.get("top_symbol_removed") or {}).get("stress_yen") or 0) > 0,
        "not_one_day": float((conc.get("best_day_removed") or {}).get("stress_yen") or 0) > 0,
        "no_pnl_rule": True,
    }
    signs = {
        "primary": float(e.get("stress_yen") or 0) > 0,
        "reverse": float((reverse.get("economics") or {}).get("stress_yen") or 0) > 0,
        "hash": float((hashed.get("economics") or {}).get("stress_yen") or 0) > 0,
    }
    fragile = not (signs["primary"] and signs["reverse"] and signs["hash"])
    if fragile:
        gates["tie_all_positive_stress"] = False
    passed = all(bool(v) for v in gates.values())
    tgt_rate = float(e.get("target_exit_rate") or 0)
    sess_n = int(e.get("session_close_n") or 0)
    n = int(e.get("trades") or 0)
    horizon = bool(n and sess_n / n >= 0.5 and tgt_rate < 0.05)
    if not passed:
        verdict = CASE_FAIL
        classification = CASE_FAIL
        nxt = NEXT_STOP
        freeze = False
    elif tgt_rate >= 0.15:
        verdict = STRATEGY_ID
        classification = CASE_PASS_STRUCTURAL
        nxt = NEXT_CONFIRM
        freeze = True
    else:
        verdict = STRATEGY_ID
        classification = CASE_PASS_CONTEXT
        nxt = NEXT_CONFIRM
        freeze = True
    target_role = "STRUCTURAL_TARGET_EXIT_MECHANISM" if tgt_rate >= 0.15 else "OPPOSING_ZONE_STRUCTURAL_CONTEXT"
    return {
        "gates": gates,
        "all_gates_pass": passed,
        "VERDICT": verdict,
        "classification": classification,
        "NEXT": nxt,
        "freeze": freeze,
        "CAP_TIE_ORDER_FRAGILE": bool(fragile),
        "tie_stress_signs": signs,
        "STRUCTURAL_EXIT_HORIZON_MISMATCH": horizon,
        "target_acts_as": target_role,
        "post_hoc_development_architecture": True,
        "d1_d4_status": "ALL_DEVELOPMENT",
        "c1_reopened": False,
        "pnl_based_rule_change": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "kabu50_applied": False,
        "highest_pnl_selected": False,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    walked = walk_bracketed(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "decision": {"VERDICT": CASE_FAIL, "NEXT": NEXT_STOP}}
    rows = list(walked.get("rows") or [])
    eligible = [r for r in rows if r.get("opposing_zone_available") and r.get("ok")]
    skips = [r for r in rows if not r.get("opposing_zone_available")]
    primary = _run(eligible, order_name="PRIMARY_SYMBOL_ASC")
    reverse = _run(eligible, order_name="REVERSE_SYMBOL_ORDER")
    hashed = _run(eligible, order_name="SEEDED_HASH_ORDER")
    conf = confound_audit(rows)
    paths = path_split(rows)
    adj = overlap_available(rows)
    decision = decide(walked=walked, primary=primary, reverse=reverse, hashed=hashed, confound=conf)
    freeze_candidate = {
        "frozen": bool(decision.get("freeze")),
        "strategy_id": STRATEGY_ID if decision.get("freeze") else None,
        "strategy_sha256": (bind.get("freeze") or {}).get("STRATEGY_SHA256"),
        "detector_sha": (bind.get("freeze") or {}).get("DETECTOR_SHA256"),
        "state_machine_sha": (bind.get("freeze") or {}).get("STATE_MACHINE_SHA256"),
        "post_hoc_development_status": True,
        "eligibility_timestamp_semantics": "decision_bar_close known at BAR_START T+1; never next-bar open",
        "ENTRY": STRATEGY_SPEC.get("entry"),
        "EXIT": STRATEGY_SPEC.get("exit"),
        "target_zone": "frozen nearest opposing active S/R at decision",
        "CAP": CAP,
        "same_symbol": 1,
        "reentry": "no same symbol x zone x day after fill",
        "lunch": "HOLD_THROUGH_LUNCH_RESUME_PM",
        "session_close": "15:20",
        "tie_ordering": "entry_eligible_time, symbol ascending",
        "cost_stress_convention": "X0_GROSS and X1_8BPS_EXECUTION_STRESS",
        "old_confirmation_still_closed": True,
        "frozen_validation_still_closed": True,
    }
    return {
        "ok": True,
        "parent_verdict": PARENT_VERDICT,
        "post_hoc_development_architecture": POST_HOC_DEVELOPMENT_ARCHITECTURE,
        "identity": {
            "walk_primary_first_test_n": int((walked.get("counts") or {}).get("primary_first_test_n") or 0),
            "expected_primary_first_test_n": 2713,
            "a2_signal_n": int((walked.get("counts") or {}).get("a2_signal_n") or 0),
        },
        "counts": walked.get("counts"),
        "same_bar_entry_n": walked.get("same_bar_entry_n"),
        "target_future_leakage_n": walked.get("target_future_leakage_n"),
        "TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N": walked.get("TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N"),
        "TARGET_ELIGIBILITY_FUTURE_BAR_N": walked.get("TARGET_ELIGIBILITY_FUTURE_BAR_N"),
        "eligibility_disagrees_with_entry_open_n": walked.get("eligibility_disagrees_with_entry_open_n"),
        "delayed_cap_entry_n": primary.get("delayed_cap_entry_n"),
        "delayed_same_symbol_entry_n": primary.get("delayed_same_symbol_entry_n"),
        "exit_retroactive_n": primary.get("exit_retroactive_n"),
        "eligible_n": len(eligible),
        "skip_n": len(skips),
        "PRIMARY": {k: v for k, v in primary.items() if k != "trades"} | {"_trades": primary["trades"]},
        "REVERSE": {k: v for k, v in reverse.items() if k != "trades"},
        "HASH": {k: v for k, v in hashed.items() if k != "trades"},
        "_rows": rows,
        "confound": conf,
        "path_mechanism": paths,
        "adjusted_diagnostic": adj,
        "decision": decision,
        "freeze_candidate": freeze_candidate,
        "strategy_spec": STRATEGY_SPEC,
        "shares": SHARES,
        "x1_stress_bps": X1_STRESS_BPS,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    e = dict((report.get("PRIMARY") or {}).get("economics") or {})
    conf = dict(report.get("confound") or {})
    conc = dict(e.get("concentration") or {})
    g = dict(d.get("gates") or {})
    return {
        "Was opposing-zone eligibility fully knowable before ENTRY?": bool(g.get("eligibility_before_entry")),
        "TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N?": report.get("TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N"),
        "eligible signal_n?": report.get("eligible_n"),
        "NO_OPPOSING_ZONE_SKIP_n?": report.get("skip_n"),
        "trade_n?": e.get("trades"),
        "CAP blocked?": e.get("cap_blocked_n"),
        "D2 stress / PF?": [(e.get("blocks") or {}).get("D2", {}).get("stress_yen"), (e.get("blocks") or {}).get("D2", {}).get("pf_stress")],
        "D3 stress / PF?": [(e.get("blocks") or {}).get("D3", {}).get("stress_yen"), (e.get("blocks") or {}).get("D3", {}).get("pf_stress")],
        "D4 stress / PF?": [(e.get("blocks") or {}).get("D4", {}).get("stress_yen"), (e.get("blocks") or {}).get("D4", {}).get("pf_stress")],
        "overall stress / PF?": [e.get("stress_yen"), e.get("pf_stress")],
        "gross/stress bps?": [e.get("mean_gross_bps"), e.get("mean_stress_bps")],
        "Does advantage remain in bps?": g.get("effect_in_bps"),
        "Any notional confound?": conf.get("notional_confound"),
        "Any market/regime proxy evidence?": conf.get("IS_TARGET_AVAILABILITY_JUST_A_PROXY"),
        "Target distance distribution?": {
            "mean_bps": e.get("mean_target_distance_bps"),
            "median_bps": e.get("median_target_distance_bps"),
        },
        "Target reached_n?": e.get("target_reached_n"),
        "Target exit rate?": e.get("target_exit_rate"),
        "Invalidation exits?": e.get("invalidation_exit_n"),
        "Session-close exits?": e.get("session_close_n"),
        "Does target act as actual EXIT or only structural context?": d.get("target_acts_as"),
        "Mean/median hold?": [e.get("mean_hold"), e.get("median_hold")],
        "Mean/median giveback?": [e.get("mean_giveback_bps"), e.get("median_giveback_bps")],
        "CAP tie-order fragile?": d.get("CAP_TIE_ORDER_FRAGILE"),
        "Top-winner distribution across symbols/days/blocks?": {
            "top_symbol": conc.get("top_symbol"),
            "best_day": conc.get("best_day"),
            "top3_symbols": conc.get("top3_symbols"),
            "top3_days": conc.get("top3_days"),
            "blocks": e.get("blocks"),
        },
        "Top symbol removal?": conc.get("top_symbol_removed"),
        "Best day removal?": conc.get("best_day_removed"),
        "Any PnL-selected additional rule?": False,
        "C1 reopened?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Kabu50 applied?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "classification": d.get("classification"),
        "STRUCTURAL_EXIT_HORIZON_MISMATCH": d.get("STRUCTURAL_EXIT_HORIZON_MISMATCH"),
        "D1/D2/D3/D4 are ALL DEVELOPMENT?": True,
    }
