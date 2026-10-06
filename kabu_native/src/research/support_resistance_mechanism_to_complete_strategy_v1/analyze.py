"""Complete-strategy economics. Descriptive gates only. No PnL rule change."""
from __future__ import annotations

from typing import Any

from research.support_resistance_mechanism_to_complete_strategy_v1 import (
    ANALYSIS_ID,
    C_HOLM_P,
    CAP,
    CAP_TIE_MATERIAL_REL,
    CASE_A,
    CASE_BOTH,
    CASE_C,
    CASE_COMBINED,
    CASE_NONE,
    EVIDENCE_A2,
    EVIDENCE_C1,
    EXPECTED_PRIMARY_FIRST_TEST_N,
    LUNCH_POLICY,
    NEXT_CONFIRM,
    NEXT_STOP,
    PARENT_VERDICT,
    SHARES,
    STRATEGY_A2,
    STRATEGY_C1,
    STRATEGY_COMBINED,
    STRATEGY_SPEC,
    X1_STRESS_BPS,
)
from research.support_resistance_mechanism_to_complete_strategy_v1.metrics import summarize, tie_fragile
from research.support_resistance_mechanism_to_complete_strategy_v1.portfolio import replay
from research.support_resistance_mechanism_to_complete_strategy_v1.walk import walk_complete


def _run_strategy(signals: list[dict[str, Any]], *, strategy_id: str) -> dict[str, Any]:
    primary = replay(signals, strategy_id=strategy_id, order_name="PRIMARY_SYMBOL_ASC")
    econ = summarize(primary["trades"], label=strategy_id)
    econ["signal_n"] = primary["signal_n"]
    econ["entry_attempts"] = primary["entry_attempts"]
    econ["filled_historical_entries"] = primary["filled_n"]
    econ["cap_blocked_n"] = primary["cap_blocked_n"]
    econ["same_symbol_blocked_n"] = primary["same_symbol_blocked_n"]
    econ["reentry_n"] = primary["reentry_n"]
    econ["occupancy_mean_at_entry"] = primary["occupancy_mean_at_entry"]
    econ["occupancy_max"] = primary["occupancy_max"]
    days = {str(t.get("date")) for t in primary["trades"]}
    econ["slot_utilization"] = (len(primary["trades"]) / (CAP * len(days))) if days else 0.0
    alts = []
    for name in ("REVERSE_SYMBOL_ORDER", "SEEDED_HASH_ORDER"):
        r = replay(signals, strategy_id=strategy_id, order_name=name)
        e = summarize(r["trades"], label=f"{strategy_id}:{name}")
        e["order_name"] = name
        e["trades_n"] = r["filled_n"]
        alts.append(e)
    frag = tie_fragile(econ | {"order_name": "PRIMARY_SYMBOL_ASC"}, alts, rel=CAP_TIE_MATERIAL_REL)
    return {
        **{k: v for k, v in primary.items() if k != "trades"},
        "trades": primary["trades"],
        "economics": econ,
        "tie": frag,
        "alt_economics": alts,
    }


def decide(a2: dict[str, Any], c1: dict[str, Any], comb: dict[str, Any]) -> dict[str, Any]:
    ea, ec, ex = a2["economics"], c1["economics"], comb["economics"]
    a2_ok = bool(ea.get("coherent"))
    c1_ok = bool(ec.get("coherent"))
    comb_ok = bool(ex.get("coherent"))
    a2_broken = float(ea.get("stress_yen") or 0) < 0 and (ea.get("pf_stress") is None or float(ea.get("pf_stress") or 0) < 1)
    c1_broken = float(ec.get("stress_yen") or 0) < 0 and (ec.get("pf_stress") is None or float(ec.get("pf_stress") or 0) < 1)
    combined_hides = (a2_ok and c1_broken and comb_ok) or (c1_ok and a2_broken and comb_ok)
    combined_adds = (
        float(ea.get("stress_yen") or 0) > 0
        and float(ec.get("stress_yen") or 0) > 0
        and not a2_broken
        and not c1_broken
        and float(ex.get("stress_yen") or 0) > max(float(ea.get("stress_yen") or 0), float(ec.get("stress_yen") or 0))
    )
    if a2_ok and c1_ok:
        verdict = CASE_BOTH
        case = "BOTH"
    elif a2_ok:
        verdict = CASE_A
        case = "A"
    elif c1_ok:
        verdict = CASE_C
        case = "C"
    elif comb_ok and not a2_broken and not c1_broken and float(ea.get("stress_yen") or 0) > 0 and float(ec.get("stress_yen") or 0) > 0:
        verdict = CASE_COMBINED
        case = "COMBINED"
    else:
        verdict = CASE_NONE
        case = "NONE"
    if combined_hides and verdict == CASE_COMBINED:
        verdict = CASE_NONE
        case = "NONE"
    freeze_ids = []
    if verdict == CASE_BOTH:
        freeze_ids = [STRATEGY_A2, STRATEGY_C1]
        if combined_adds:
            freeze_ids.append(STRATEGY_COMBINED)
    elif verdict == CASE_A:
        freeze_ids = [STRATEGY_A2]
    elif verdict == CASE_C:
        freeze_ids = [STRATEGY_C1]
    elif verdict == CASE_COMBINED:
        freeze_ids = [STRATEGY_COMBINED]
    nxt = NEXT_CONFIRM if verdict != CASE_NONE else NEXT_STOP
    return {
        "case": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "A2_complete_strategy_candidate": a2_ok,
        "C1_complete_strategy_candidate": c1_ok,
        "combined_adds_genuine_portfolio_value": bool(combined_adds) and not combined_hides,
        "combined_hides_failed_component": bool(combined_hides),
        "freeze_strategy_ids": freeze_ids,
        "evidence_A2": EVIDENCE_A2,
        "evidence_C1": EVIDENCE_C1,
        "c_holm_p": C_HOLM_P,
        "A_and_C_identical_statistical_strength": False,
        "pnl_based_rule_change": False,
        "highest_pnl_selected": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "kabu50_applied": False,
        "matchability_used_as_entry_filter": False,
        "extra_entry_filters": False,
        "extra_exit_search": False,
        "a1_merged_with_a2": False,
        "lunch_policy": LUNCH_POLICY,
        "cap": CAP,
        "shares": SHARES,
        "x1_stress_bps": X1_STRESS_BPS,
        "CAP_TIE_ORDER_FRAGILE": bool(
            (a2.get("tie") or {}).get("CAP_TIE_ORDER_FRAGILE")
            or (c1.get("tie") or {}).get("CAP_TIE_ORDER_FRAGILE")
            or (comb.get("tie") or {}).get("CAP_TIE_ORDER_FRAGILE")
        ),
    }


def _econ_answers(prefix: str, e: dict[str, Any]) -> dict[str, Any]:
    return {
        f"{prefix} signal_n?": e.get("signal_n"),
        f"{prefix} trade_n?": e.get("trades"),
        f"{prefix} gross PnL?": e.get("gross_yen"),
        f"{prefix} 8bps-stress PnL?": e.get("stress_yen"),
        f"{prefix} PF gross/stress?": [e.get("pf_gross"), e.get("pf_stress")],
        f"{prefix} median trade?": e.get("median_trade"),
        f"{prefix} maxDD?": e.get("maxDD_yen"),
        f"{prefix} positive-day rate?": e.get("positive_day_rate"),
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    a2 = dict((report.get("A2") or {}).get("economics") or {})
    c1 = dict((report.get("C1") or {}).get("economics") or {})
    comb = dict((report.get("COMBINED") or {}).get("economics") or {})
    safety = dict(report.get("safety") or {})
    a1 = dict(report.get("A1") or {})
    conc_a2 = dict(a2.get("concentration") or {})
    return {
        **_econ_answers("A2", a2),
        **_econ_answers("C1", c1),
        **_econ_answers("COMBINED", comb),
        "How many target exits?": {
            "A2": a2.get("target_exit_n"),
            "C1": c1.get("target_exit_n"),
            "COMBINED": comb.get("target_exit_n"),
        },
        "How many invalidation exits?": {
            "A2": a2.get("invalidation_exit_n"),
            "C1": c1.get("invalidation_exit_n"),
            "COMBINED": comb.get("invalidation_exit_n"),
        },
        "How many session-close exits?": {
            "A2": a2.get("session_close_n"),
            "C1": c1.get("session_close_n"),
            "COMBINED": comb.get("session_close_n"),
        },
        "Any target future leakage?": int(report.get("target_future_leakage_n") or 0),
        "Any same-bar entry?": int(report.get("same_bar_entry_n") or 0),
        "Any delayed CAP entry?": int(report.get("delayed_cap_entry_n") or 0),
        "Any delayed same-symbol entry?": int(report.get("delayed_same_symbol_entry_n") or 0),
        "CAP tie-order fragile?": d.get("CAP_TIE_ORDER_FRAGILE"),
        "D1/D2/D3/D4 economics?": {
            "A2": a2.get("blocks"),
            "C1": c1.get("blocks"),
            "COMBINED": comb.get("blocks"),
        },
        "LONG vs SHORT?": {
            "A2": {"LONG": a2.get("LONG"), "SHORT": a2.get("SHORT")},
            "C1": {"LONG": c1.get("LONG"), "SHORT": c1.get("SHORT")},
        },
        "support vs resistance?": {
            "A2": {"SUPPORT": a2.get("SUPPORT"), "RESISTANCE": a2.get("RESISTANCE")},
            "C1": {"SUPPORT": c1.get("SUPPORT"), "RESISTANCE": c1.get("RESISTANCE")},
        },
        "A1 minute fillability and economics?": a1,
        "Break-even cost bps?": {
            "A2": a2.get("break_even_cost_bps"),
            "C1": c1.get("break_even_cost_bps"),
            "COMBINED": comb.get("break_even_cost_bps"),
        },
        "Top5% winner removal result?": {
            "A2": (conc_a2.get("top5pct_winners_removed") or {}),
            "C1": ((c1.get("concentration") or {}).get("top5pct_winners_removed") or {}),
            "COMBINED": ((comb.get("concentration") or {}).get("top5pct_winners_removed") or {}),
        },
        "Top symbol removal?": {
            "A2": conc_a2.get("top_symbol_removed"),
            "C1": (c1.get("concentration") or {}).get("top_symbol_removed"),
            "COMBINED": (comb.get("concentration") or {}).get("top_symbol_removed"),
        },
        "Best day removal?": {
            "A2": conc_a2.get("best_day_removed"),
            "C1": (c1.get("concentration") or {}).get("best_day_removed"),
            "COMBINED": (comb.get("concentration") or {}).get("best_day_removed"),
        },
        "Does A2 remain a Complete Strategy candidate?": d.get("A2_complete_strategy_candidate"),
        "Does C1?": d.get("C1_complete_strategy_candidate"),
        "Does combined add genuine portfolio value?": d.get("combined_adds_genuine_portfolio_value"),
        "Any PnL-based rule change?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Kabu50 applied?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "SAME_BAR_ENTRY_N": safety.get("SAME_BAR_ENTRY_N"),
        "TARGET_FUTURE_LEAKAGE_N": safety.get("TARGET_FUTURE_LEAKAGE_N"),
        "WRITE_OVERLAP_N": d.get("WRITE_OVERLAP_N"),
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    walked = walk_complete(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "decision": {"VERDICT": CASE_NONE, "NEXT": NEXT_STOP}}
    a2 = _run_strategy(list(walked.get("a2_signals") or []), strategy_id=STRATEGY_A2)
    c1 = _run_strategy(list(walked.get("c1_signals") or []), strategy_id=STRATEGY_C1)
    comb = _run_strategy(list(walked.get("a2_signals") or []) + list(walked.get("c1_signals") or []), strategy_id=STRATEGY_COMBINED)
    decision = decide(a2, c1, comb)
    same_bar = int(walked.get("same_bar_entry_n") or 0)
    leak = int(walked.get("target_future_leakage_n") or 0)
    delayed_cap = int(a2["delayed_cap_entry_n"] + c1["delayed_cap_entry_n"] + comb["delayed_cap_entry_n"])
    delayed_sym = int(a2["delayed_same_symbol_entry_n"] + c1["delayed_same_symbol_entry_n"] + comb["delayed_same_symbol_entry_n"])
    retro = int(a2["exit_retroactive_n"] + c1["exit_retroactive_n"] + comb["exit_retroactive_n"])
    identity_ok = int((walked.get("counts") or {}).get("primary_first_test_n") or 0) == int(EXPECTED_PRIMARY_FIRST_TEST_N)
    freeze_candidate = {
        "frozen": bool(decision["freeze_strategy_ids"]) and decision["VERDICT"] != CASE_NONE,
        "strategy_ids": decision["freeze_strategy_ids"],
        "detector_sha": (bind.get("freeze") or {}).get("DETECTOR_SHA256"),
        "state_machine_sha": (bind.get("freeze") or {}).get("STATE_MACHINE_SHA256"),
        "strategy_sha256": (bind.get("freeze") or {}).get("STRATEGY_SHA256"),
        "ENTRY_rules": STRATEGY_SPEC.get("a2") + " | " + STRATEGY_SPEC.get("c1"),
        "EXIT_rules": "structural_target | thesis_invalidation | 15:20",
        "CAP": CAP,
        "same_symbol": 1,
        "reentry": STRATEGY_SPEC.get("reentry"),
        "lunch": LUNCH_POLICY,
        "session_close": "15:20",
        "cost_stress_convention": "X0_GROSS and X1_8BPS_EXECUTION_STRESS",
        "tie_order_convention": STRATEGY_SPEC.get("tie_order"),
        "old_confirmation_still_closed": True,
        "frozen_validation_still_closed": True,
    }
    return {
        "ok": True,
        "parent_verdict": PARENT_VERDICT,
        "identity": {
            "walk_primary_first_test_n": int((walked.get("counts") or {}).get("primary_first_test_n") or 0),
            "expected_primary_first_test_n": EXPECTED_PRIMARY_FIRST_TEST_N,
            "identity_ok": identity_ok,
        },
        "counts": walked.get("counts"),
        "same_bar_entry_n": same_bar,
        "target_future_leakage_n": leak,
        "delayed_cap_entry_n": delayed_cap,
        "delayed_same_symbol_entry_n": delayed_sym,
        "exit_retroactive_n": retro,
        "A2": {k: v for k, v in a2.items() if k != "trades"} | {"_trades": a2["trades"]},
        "C1": {k: v for k, v in c1.items() if k != "trades"} | {"_trades": c1["trades"]},
        "COMBINED": {k: v for k, v in comb.items() if k != "trades"} | {"_trades": comb["trades"]},
        "_a2_signals": walked.get("a2_signals"),
        "_c1_signals": walked.get("c1_signals"),
        "_a1_rows": walked.get("a1_rows"),
        "A1": walked.get("a1_diagnostic"),
        "decision": decision,
        "freeze_candidate": freeze_candidate,
        "strategy_spec": STRATEGY_SPEC,
        "analysis_id": ANALYSIS_ID,
        "lunch_policy": LUNCH_POLICY,
    }
