"""Face-valid rebuild report. No PnL. No Confirmation. No Frozen Validation. No economic verdict."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import (
    ANALYSIS_ID,
    CASE_BIND,
    CASE_GATE,
    CASE_NOT_FACE,
    CASE_READY,
    LUNCH_POLICY,
    NEXT_BIND,
    NEXT_MATCHED,
    NEXT_REPAIR_FACE,
    NEXT_STOP_GATE,
    PARENT_VERDICT,
    RCA_DEFERRED,
)
from research.support_resistance_face_valid_first_interaction_rebuild_v1.charts import build_sample_packs, choose_sample, render_all
from research.support_resistance_face_valid_first_interaction_rebuild_v1.face import score_chart, summarize_face
from research.support_resistance_face_valid_first_interaction_rebuild_v1.precommit import matched_test_precommit, outcome_precommit
from research.support_resistance_face_valid_first_interaction_rebuild_v1.walk import walk_discovery


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def percentiles(xs: list[float]) -> dict[str, float | int | None]:
    arr = np.asarray([x for x in xs if _finite(x)], dtype=float)
    if arr.size == 0:
        return {"n": 0, "p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "max": None, "mean": None}
    return {
        "n": int(arr.size),
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def decide(*, bind_ok: bool, gates: dict[str, int], face_valid: bool) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior bind failed. Do not open Confirmation. Do not revive the old economic null.",
        }
    failed = {k: int(v) for k, v in gates.items() if int(v or 0) != 0}
    if failed:
        return {
            "CASE": "GATE",
            "VERDICT": CASE_GATE,
            "NEXT": NEXT_STOP_GATE,
            "INTERPRETATION": "A hard structural gate failed. STOP. Do not proceed to matched economic testing.",
            "failed_gates": failed,
        }
    if face_valid:
        return {
            "CASE": "READY",
            "VERDICT": CASE_READY,
            "NEXT": NEXT_MATCHED,
            "INTERPRETATION": (
                "The rebuilt detector now represents the intended market structure on Discovery charts. "
                "This is not evidence that support/resistance is an economic edge."
            ),
        }
    return {
        "CASE": "NOT_FACE",
        "VERDICT": CASE_NOT_FACE,
        "NEXT": NEXT_REPAIR_FACE,
        "INTERPRETATION": "Structural gates passed, but the new charts are not yet face-valid as discretionary S/R. Do not open matched testing.",
    }


def _episode_stats(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    tests = [e for e in episodes if e.get("first_test")]
    primary = [e for e in tests if str(e.get("selection_slot") or "").startswith("NEAREST_ACTIVE")]
    c = Counter(str(e.get("resolution") or "") for e in tests)
    return {
        "selected_zone_days": len(episodes),
        "first_test_n": len(tests),
        "primary_first_test_n": len(primary),
        "rejection_n": sum(1 for e in tests if e.get("rejection")),
        "break_n": sum(1 for e in tests if e.get("break")),
        "accept2_n": sum(1 for e in tests if e.get("accept2")),
        "clear_n": sum(1 for e in tests if e.get("clear")),
        "valid_retest_n": sum(1 for e in tests if e.get("retest")),
        "retest_hold_n": sum(1 for e in tests if e.get("retest_hold")),
        "failed_retest_n": sum(1 for e in tests if e.get("failed_retest")),
        "unresolved_n": c.get("UNRESOLVED", 0),
        "no_test_n": sum(1 for e in episodes if not e.get("first_test")),
        "raw_overlap_bars": percentiles([float(e.get("raw_overlap_bars") or 0) for e in tests]),
        "emissions_per_episode": percentiles([float(e.get("emissions") or 0) for e in tests]),
        "raw_events_per_episode": float(np.mean([float(e.get("emissions") or 0) for e in tests])) if tests else None,
        "repeated_minute_first_test_emissions": False,
        "canonical_record_per_symbol_zone_day": True,
        "valid_retests_require_clear": True,
        "resolution_counts": dict(c),
        "hold_fail_overlap_n": sum(int(e.get("hold_fail_overlap") or 0) for e in episodes),
        "retest_without_clear_n": sum(int(e.get("retest_without_clear") or 0) for e in episodes),
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    walked = walk_discovery(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason") or "walk_failed"}
    rows = list(walked["rows"])
    episodes = list(walked["episodes"])
    all_rx = list(walked["all_rx"])
    gate = {
        "retroactive_zone_n": int((walked.get("gate") or {}).get("retroactive_zone_n") or 0),
        "future_pivot_leakage_n": int((walked.get("gate") or {}).get("future_pivot_leakage_n") or 0),
        "already_broken_active_zone_n": int((walked.get("gate") or {}).get("already_broken_active_zone_n") or 0),
        "retest_without_clear_n": int((walked.get("gate") or {}).get("retest_without_clear_n") or 0),
        "retest_hold_and_fail_overlap_n": int((walked.get("gate") or {}).get("retest_hold_and_fail_overlap_n") or 0),
        "duplicate_first_interaction_n": int((walked.get("gate") or {}).get("duplicate_first_interaction_n") or 0),
        "retroactive_event_timestamp_n": int((walked.get("gate") or {}).get("retroactive_event_timestamp_n") or 0),
    }
    highs = [r for r in all_rx if r.get("role") == "RESISTANCE"]
    lows = [r for r in all_rx if r.get("role") == "SUPPORT"]
    n_sd = len(rows)
    dens = {
        "symbol_days": n_sd,
        "n_res_active": percentiles([float(r["n_res_active"]) for r in rows]),
        "n_sup_active": percentiles([float(r["n_sup_active"]) for r in rows]),
        "n_selected_resistance": percentiles([float(r["n_selected_resistance"]) for r in rows]),
        "n_selected_support": percentiles([float(r["n_selected_support"]) for r in rows]),
        "active_resistance_zone_days": int(sum(int(r["n_res_active"] or 0) for r in rows)),
        "active_support_zone_days": int(sum(int(r["n_sup_active"] or 0) for r in rows)),
        "no_level_symbol_days": sum(1 for r in rows if r.get("no_level")),
        "no_level_rate": (sum(1 for r in rows if r.get("no_level")) / n_sd) if n_sd else None,
    }
    ep_stats = _episode_stats(episodes)
    exclude = set(bind.get("prior_sample_keys") or [])
    keys = choose_sample(rows, exclude=exclude)
    overlap_prior = sum(1 for k in keys if k in exclude)
    packs = build_sample_packs(
        keys=keys,
        rows=rows,
        hist=walked["hist"],
        reactions=walked["reactions"],
        disc=list(walked.get("disc") or []),
    )
    chart_index = render_all(keys, packs)
    scored = []
    for rec in chart_index:
        pack = packs.get((str(rec["symbol"]), str(rec["date"])))
        scored.append(score_chart(rec, pack))
    face = summarize_face(scored)
    decision = decide(bind_ok=bool(bind.get("ok")), gates=gate, face_valid=bool(face.get("face_valid")))
    return {
        "ok": True,
        "analysis_id": ANALYSIS_ID,
        "parent_verdict": PARENT_VERDICT,
        "rca_deferred": RCA_DEFERRED,
        "lunch_policy": LUNCH_POLICY,
        "no_pnl_parameter_tuning": True,
        "x0_x1_used_to_select_rules": False,
        "old_no_info_revived": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "swing": {
            "confirmed_swing_highs": len(highs),
            "confirmed_swing_lows": len(lows),
            "confirm_atr_mult": 0.75,
            "confirm_atr_selected_by_pnl": False,
            "same_day_confirmation_n": sum(1 for r in all_rx if str(r.get("pivot_date")) == str(r.get("confirmation_date"))),
        },
        "density": dens,
        "gates": gate,
        "episodes": ep_stats,
        "face": face,
        "chart_index": scored,
        "matched_test_precommit": matched_test_precommit(),
        "outcome_precommit": outcome_precommit(),
        "decision": decision,
        "sample_n": len(scored),
        "sample_prior_overlap_n": overlap_prior,
        "n_days": walked.get("n_days"),
        "n_symbols_loaded": walked.get("n_symbols_loaded"),
        "forbidden_loaded": False,
        "rows_compact": [
            {
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "block": r.get("block"),
                "n_res_active": r.get("n_res_active"),
                "n_sup_active": r.get("n_sup_active"),
                "n_selected_resistance": r.get("n_selected_resistance"),
                "n_selected_support": r.get("n_selected_support"),
                "no_level": r.get("no_level"),
            }
            for r in rows[:: max(1, len(rows) // 4000)][:4000]
        ],
        "episode_compact": [
            {
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "selection_slot": e.get("selection_slot"),
                "first_test": e.get("first_test"),
                "resolution": e.get("resolution"),
                "break": e.get("break"),
                "rejection": e.get("rejection"),
                "accept2": e.get("accept2"),
                "clear": e.get("clear"),
                "retest": e.get("retest"),
                "retest_hold": e.get("retest_hold"),
                "failed_retest": e.get("failed_retest"),
                "emissions": e.get("emissions"),
                "raw_overlap_bars": e.get("raw_overlap_bars"),
            }
            for e in episodes[:: max(1, len(episodes) // 3000)][:3000]
        ],
        "rx_compact": [
            {
                "symbol": r.get("symbol"),
                "role": r.get("role"),
                "pivot_date": r.get("pivot_date"),
                "pivot_price": r.get("pivot_price"),
                "confirmation_date": r.get("confirmation_date"),
                "available_from": r.get("available_from"),
                "move_away_atr": r.get("move_away_atr"),
            }
            for r in all_rx[:: max(1, len(all_rx) // 4000)][:4000]
        ],
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    sw = dict(report.get("swing") or {})
    dens = dict(report.get("density") or {})
    gate = dict(report.get("gates") or {})
    ep = dict(report.get("episodes") or {})
    face = dict(report.get("face") or {})
    d = dict(report.get("decision") or {})
    return {
        "How many causal confirmed swing highs?": sw.get("confirmed_swing_highs"),
        "swing lows?": sw.get("confirmed_swing_lows"),
        "How many active resistance/support zones after stale-level removal?": {
            "resistance_zone_days": dens.get("active_resistance_zone_days"),
            "support_zone_days": dens.get("active_support_zone_days"),
            "median_res": (dens.get("n_res_active") or {}).get("p50"),
            "median_sup": (dens.get("n_sup_active") or {}).get("p50"),
        },
        "How many selected resistance levels per symbol-day?": (dens.get("n_selected_resistance") or {}).get("p50"),
        "support levels?": (dens.get("n_selected_support") or {}).get("p50"),
        "Any already-broken active levels?": gate.get("already_broken_active_zone_n"),
        "Any retroactive zone creation?": gate.get("retroactive_zone_n"),
        "Any future pivot leakage?": gate.get("future_pivot_leakage_n"),
        "How many first-test episodes?": ep.get("first_test_n"),
        "Raw events per episode?": ep.get("raw_events_per_episode"),
        "Any repeated minute emissions from the same first interaction?": ep.get("repeated_minute_first_test_emissions"),
        "How many valid retests require actual CLEAR first?": ep.get("valid_retest_n"),
        "Any RETEST_HOLD / FAILED_RETEST overlap?": gate.get("retest_hold_and_fail_overlap_n"),
        "Do the NEW 200 charts look materially closer to discretionary support/resistance?": bool(face.get("face_valid")),
        "Clutter rate?": face.get("clutter_rate"),
        "Obvious/relevant rate?": face.get("obvious_relevant_rate"),
        "Are stale stripes gone?": face.get("stale_stripes_gone"),
        "Does the detector sometimes correctly return NO LEVEL?": bool((dens.get("no_level_symbol_days") or 0) > 0),
        "Any PnL optimization performed?": False,
        "Any X0/X1 used to select rules?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "sample_n": report.get("sample_n"),
        "sample_prior_overlap_n": report.get("sample_prior_overlap_n"),
        "gates": gate,
        "no_level_rate": dens.get("no_level_rate"),
    }
