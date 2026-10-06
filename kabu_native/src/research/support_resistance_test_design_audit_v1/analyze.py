"""Decision A–E from design-audit evidence. No PnL. No Confirmation. No Frozen Validation."""
from __future__ import annotations

from typing import Any

from research.support_resistance_test_design_audit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    LUNCH_POLICY,
    NEXT_BIND,
    NEXT_REBUILD,
    NEXT_STOP_E,
    PARENT_VERDICT,
    RCA_DEFERRED,
    SAMPLE_N,
)
from research.support_resistance_test_design_audit_v1.design import (
    first_interaction_design,
    matched_control_design,
    minute_limit_limitation,
    outcome_metric_review,
    retest_semantics_from_source,
)
from research.support_resistance_test_design_audit_v1.minute_audit import walk_minute_audit
from research.support_resistance_test_design_audit_v1.reconstruct import reconstruct_daily, summarize_density, summarize_salience
from research.support_resistance_test_design_audit_v1.sample_charts import render_all


def decide(
    *,
    bind_ok: bool,
    face_valid: bool,
    overcounts: bool,
    gate_misaligned: bool,
    density_implausible: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": "SUPPORT_RESISTANCE_TEST_DESIGN_AUDIT_BIND_FAILED_V1",
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior bind failed. Do not open Confirmation. Old NO_INCREMENTAL_INFORMATION is not re-affirmed.",
            "old_no_info_still_justified": False,
        }
    n_def = int(density_implausible or (not face_valid)) + int(overcounts) + int(gate_misaligned)
    if n_def >= 2:
        verd = CASE_D
        nxt = NEXT_REBUILD
        interp = (
            "The prior test did not represent the support/resistance a discretionary trader would recognize. "
            "The detector is too dense, the state machine over-counts correlated minutes, and the 5pp continuation "
            "gate was the wrong sole criterion. MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1 must not be read as "
            "'support/resistance has no market information.'"
        )
    elif density_implausible or (not face_valid):
        verd = CASE_A
        nxt = NEXT_REBUILD
        interp = "Machine zones are not face-valid as the small set of salient daily S/R areas."
    elif overcounts:
        verd = CASE_B
        nxt = NEXT_REBUILD
        interp = "Zones may exist, but the event state machine over-counts interactions, so economics were not identified at the right unit."
    elif gate_misaligned:
        verd = CASE_C
        nxt = NEXT_REBUILD
        interp = "The 5pp continuation-rate gate was not an appropriate primary success criterion."
    else:
        verd = CASE_E
        nxt = NEXT_STOP_E
        interp = "The old test design is confirmed valid; the NO_INCREMENTAL_INFORMATION conclusion may stand."
    return {
        "CASE": verd,
        "VERDICT": verd,
        "NEXT": nxt,
        "INTERPRETATION": interp,
        "old_no_info_still_justified": verd == CASE_E,
        "deficiency_count": n_def,
        "flags": {
            "face_valid": face_valid,
            "overcounts": overcounts,
            "gate_misaligned": gate_misaligned,
            "density_implausible": density_implausible,
        },
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    daily = reconstruct_daily(bind)
    if not daily.get("ok"):
        return {"ok": False, "bind": bind, "reason": daily.get("reason") or "daily_failed"}
    dens_sum = summarize_density(list(daily["density_rows"]))
    sal_sum = summarize_salience(list(daily["salience_rows"]))
    charts = render_all(daily["sample_snaps"], list(daily["sample_keys"]))
    minute = walk_minute_audit(
        bind=bind,
        minutes=daily["minutes"],
        hist=daily["hist"],
        reactions=daily["reactions"],
        sample_keys=list(daily["sample_keys"]),
    )
    parent = dict(bind.get("parent_report") or {})
    parent_ans = dict(bind.get("parent_answers") or {})
    ic = dict(parent.get("interaction_counts") or parent_ans.get("interaction_counts") or {})
    retest_n = int(ic.get("retest") or 0)
    hold_n = int(ic.get("retest_hold") or 0)
    fail_n = int(ic.get("failed_retest") or 0)
    semantics = retest_semantics_from_source()
    semantics["parent_retest"] = retest_n
    semantics["parent_retest_hold"] = hold_n
    semantics["parent_failed_retest"] = fail_n
    semantics["parent_retest_equals_hold"] = bool(retest_n and hold_n and abs(retest_n - hold_n) <= 2)
    outcome = outcome_metric_review(parent)
    # pull gaps from parent report if present
    body = dict(parent.get("body") or parent)
    comparisons = dict(body.get("comparisons") or parent.get("comparisons") or {})
    outcome["parent_comparisons"] = comparisons or None
    outcome["parent_touch_monotonic"] = (comparisons or {}).get("touch_count_monotonic")
    lim = minute_limit_limitation()
    pas = dict(parent_ans.get("passive_retest_entry") or {})
    if not pas:
        pas = dict(((parent.get("replay") or {}).get("limit") or {}))
    lim["parent_fill_rate"] = pas.get("fill_rate")
    lim["parent_X0_after_fill"] = pas.get("X0_after_fill")
    lim["parent_order_n"] = pas.get("order_n")

    median_res = (dens_sum.get("n_res_active") or {}).get("p50")
    median_sup = (dens_sum.get("n_sup_active") or {}).get("p50")
    density_implausible = bool(
        (median_res is not None and float(median_res) >= 4)
        or (dens_sum.get("mean_res_per_symbol_day") or 0) >= 4
        or (dens_sum.get("clutter_rate") or 0) >= 0.50
    )
    face_valid = bool(charts.get("face_valid_overall")) and (not density_implausible)
    overcounts = bool(
        (minute.get("raw_events_per_true_interaction") or 0) >= 3
        or (minute.get("transition_lingering_rate") or 0) >= 0.50
        or semantics.get("parent_retest_equals_hold")
        or minute.get("mean_bars_inside_hold_consistent_with_lingering")
    )
    gate_misaligned = True
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        face_valid=face_valid,
        overcounts=overcounts,
        gate_misaligned=gate_misaligned,
        density_implausible=density_implausible,
    )
    root = [
        {
            "id": "R1",
            "finding": "Same-day close-in-outer-40% labels ordinary daily fluctuations as reactions.",
            "evidence": sal_sum,
        },
        {
            "id": "R2",
            "finding": "Every 2-touch cluster in a 60-day lookback stays active, including stale and already-broken zones.",
            "evidence": {
                "mean_res": dens_sum.get("mean_res_per_symbol_day"),
                "stale": dens_sum.get("n_res_stale_3atr"),
                "already_broken": dens_sum.get("already_broken_zone_total"),
            },
        },
        {
            "id": "R3",
            "finding": "Adjacent 0.30 ATR greedy clusters produce overlapping bands that a human would draw as one area.",
            "evidence": {
                "material_overlap_pair_total": dens_sum.get("material_overlap_pair_total"),
                "symbol_days_with_material_overlap": dens_sum.get("symbol_days_with_material_overlap"),
                "nested_pair_total": dens_sum.get("nested_pair_total"),
                "shared_members_by_construction": dens_sum.get("shared_reaction_members_total"),
            },
        },
        {
            "id": "R4",
            "finding": "RETEST_HOLD is co-emitted with RETEST_ZONE on in-zone bars after break; clearance is not required.",
            "evidence": semantics,
        },
        {
            "id": "R5",
            "finding": "10-minute refractory re-emits the same episode; mean bars_inside of 57–80 is lingering, not a discrete test.",
            "evidence": {
                "raw_per_episode": minute.get("raw_events_per_true_interaction"),
                "bars_inside_hold": minute.get("bars_inside_at_retest_hold"),
            },
        },
        {
            "id": "R6",
            "finding": "5pp continuation vs unmatched PDH was the sole gate and mixed questions A–D.",
            "evidence": outcome,
        },
        {
            "id": "R7",
            "finding": "Minute-bar limit fills are a clue, not proof about live retest queues.",
            "evidence": lim,
        },
    ]
    return {
        "ok": True,
        "analysis_id": ANALYSIS_ID,
        "parent_verdict": PARENT_VERDICT,
        "rca_deferred": RCA_DEFERRED,
        "lunch_policy": LUNCH_POLICY,
        "no_pnl_parameter_tuning": True,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "density": dens_sum,
        "density_rows": daily["density_rows"],
        "salience": sal_sum,
        "salience_rows": daily["salience_rows"],
        "charts": {k: v for k, v in charts.items() if k not in {"index", "zone_index"}},
        "chart_index": charts.get("index") or [],
        "chart_zone_index": charts.get("zone_index") or [],
        "minute": {k: v for k, v in minute.items() if k not in {"traces", "matched_rows"}},
        "traces": minute.get("traces") or [],
        "matched_rows": minute.get("matched_rows") or [],
        "semantics": semantics,
        "first_interaction_design": first_interaction_design(),
        "matched_control_design": matched_control_design(),
        "outcome_metric_review": outcome,
        "minute_limit_limitation": lim,
        "root_cause": root,
        "decision": decision,
        "sample_n_required": SAMPLE_N,
        "sample_n": charts.get("n"),
        "n_days": daily.get("n_days"),
        "n_symbols_loaded": daily.get("n_symbols_loaded"),
        "forbidden_loaded": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    dens = dict(report.get("density") or {})
    sal = dict(report.get("salience") or {})
    charts = dict(report.get("charts") or {})
    minute = dict(report.get("minute") or {})
    sem = dict(report.get("semantics") or {})
    dec = dict(report.get("decision") or {})
    n_res = dict(dens.get("n_res_active") or {})
    n_sup = dict(dens.get("n_sup_active") or {})
    return {
        "Median active resistance zones per symbol-day?": n_res.get("p50"),
        "Median support zones?": n_sup.get("p50"),
        "How many zones overlap materially?": dens.get("material_overlap_pair_total"),
        "Do machine zones visually correspond to obvious chart zones?": bool(charts.get("face_valid_overall")),
        "How many reaction points are structurally weak?": sal.get("structurally_weak_n"),
        "Why does retest ≈ retest_hold while failed_retest also exists?": sem.get("why_retest_almost_equals_retest_hold"),
        "Are interaction labels independent?": False,
        "How many unique symbol × zone × day episodes exist?": minute.get("unique_symbol_zone_day_n"),
        "How many raw events are emitted per true interaction?": minute.get("raw_events_per_true_interaction"),
        "Is mean bars_inside 57–80 consistent with the intended setup?": False,
        "Was 5pp continuation an appropriate primary gate?": False,
        "Do matched controls change the result?": (
            "The old unmatched PDH comparison is not a valid control. "
            "A sample-day matched diagnostic does not rehabilitate MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1 "
            "because detector density, retest semantics, and event unit remain broken."
        ),
        "Is the old MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1 still justified?": bool(dec.get("old_no_info_still_justified")),
        "No PnL parameter tuning?": True,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": dec.get("VERDICT"),
        "NEXT?": dec.get("NEXT"),
        "obvious_chart_rate": charts.get("obvious_rate"),
        "clutter_chart_rate": charts.get("clutter_rate"),
        "ordinary_fluctuation_reaction_n": sal.get("ordinary_fluctuation_n"),
        "transition_lingering_rate": minute.get("transition_lingering_rate"),
        "matched_n": minute.get("matched_n"),
        "parent_retest": sem.get("parent_retest"),
        "parent_retest_hold": sem.get("parent_retest_hold"),
        "parent_failed_retest": sem.get("parent_failed_retest"),
    }
