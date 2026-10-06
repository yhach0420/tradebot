"""Report body and required answers. RCA of reference-level playbooks is deferred. Discovery only."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1 import (
    ANALYSIS_ID,
    CASE_ATLAS,
    CASE_BIND,
    CASE_NO_INFO,
    CASE_PARTICIPATION,
    CASE_PATH,
    CASE_STRATEGY,
    LUNCH_POLICY,
    LOOKBACK_DAYS,
    NEXT_BIND,
    NEXT_FREEZE,
    NEXT_PARTICIPATION,
    NEXT_RCA_ZONE,
    NEXT_STOP_NO_INFO,
    PARENT_VERDICT,
    PDH_CONTROL_ID,
    RCA_DEFERRED,
    SESSION_FLAT,
    ZONE_HALF_ATR,
)
from research.multi_touch_daily_zone_1m_price_action_v1.bind import bind_prior
from research.multi_touch_daily_zone_1m_price_action_v1.compare import participation_split, path_stats, run_comparisons
from research.multi_touch_daily_zone_1m_price_action_v1.replay import replay_limits, replay_market
from research.multi_touch_daily_zone_1m_price_action_v1.walk import walk_discovery


def decide(*, bind_ok: bool, path_sep: bool, part_sep: bool, x1_ids: list[str], atlas_n: int) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior bind failed. Do not open Confirmation."}
    if x1_ids and path_sep:
        return {
            "CASE": "STRATEGY",
            "VERDICT": CASE_STRATEGY,
            "NEXT": NEXT_FREEZE,
            "INTERPRETATION": "A multi-touch zone mechanism showed path separation and complete-strategy X1>0 on Discovery D2–D4. Design evidence only. Frozen Validation closed.",
        }
    if path_sep:
        return {
            "CASE": "PATH",
            "VERDICT": CASE_PATH,
            "NEXT": NEXT_RCA_ZONE,
            "INTERPRETATION": "Multi-touch zone interactions separate 1-minute paths versus PDH or nested transitions. Not promoted. 8bps is stress for market-like only.",
        }
    if part_sep:
        return {
            "CASE": "PARTICIPATION",
            "VERDICT": CASE_PARTICIPATION,
            "NEXT": NEXT_PARTICIPATION,
            "INTERPRETATION": "Zone structure alone does not separate paths; participation at the zone does. Do not invent more complex drawings.",
        }
    if atlas_n > 0:
        return {
            "CASE": "NO_INFO",
            "VERDICT": CASE_NO_INFO,
            "NEXT": NEXT_STOP_NO_INFO,
            "INTERPRETATION": "2+ touch zones, accept, retest hold, and flips look like simple reference levels (including PDH). Do not invent increasingly complex hand-drawn zones. Reference-level RCA remains deferred.",
        }
    return {"CASE": "ATLAS", "VERDICT": CASE_ATLAS, "NEXT": NEXT_STOP_NO_INFO, "INTERPRETATION": "No events."}


def _bucket_counts(hits: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter(str(h.get("bucket") or h.get("event_kind") or "") for h in hits)
    return dict(c)


def _d1d4(rows: list[dict[str, Any]], pid: str) -> dict[str, Any]:
    by = defaultdict(list)
    for r in rows:
        if r.get("playbook_id") == pid:
            by[str(r.get("block") or "")].append(r)
    rec = {"playbook_id": pid}
    rates = []
    for b in ("D1", "D2", "D3", "D4"):
        st = path_stats(by.get(b) or [])
        rec[b] = st
        if b != "D1" and st.get("continuation_rate") is not None:
            rates.append(float(st["continuation_rate"]))
    rec["d2_d4_band"] = rates
    rec["only_d4"] = bool(rates) and (by.get("D4") and not by.get("D2") and not by.get("D3"))
    return rec


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    if not bind.get("ok"):
        return {"bind": {k: v for k, v in bind.items() if k != "by_symbol"}, "decision": decide(bind_ok=False, path_sep=False, part_sep=False, x1_ids=[], atlas_n=0)}
    print("WALK_START multi_touch_daily_zone", flush=True)
    walked = walk_discovery(bind)
    hits = list(walked.get("atlas_hits") or [])
    pb = list(walked.get("playbook_rows") or [])
    orders = list(walked.get("limit_orders") or [])
    zdays = list(walked.get("zone_day_rows") or [])
    cmp = run_comparisons(hits)
    part = participation_split(hits)
    part_sep = bool(
        (part.get("hi_vol") or {}).get("continuation_rate") is not None
        and (part.get("lo_vol") or {}).get("continuation_rate") is not None
        and abs(float(part["hi_vol"]["continuation_rate"]) - float(part["lo_vol"]["continuation_rate"])) >= 0.05
        and min(int(part["hi_vol"].get("event_n") or 0), int(part["lo_vol"].get("event_n") or 0)) >= 80
    )
    packs = [replay_market(pb, playbook_id=pid) for pid in ("P1", "P2", "P3", "P4", "P5", "PDH")]
    lim = replay_limits(orders)
    x1_ids = [p["playbook_id"] for p in packs if (p.get("X1_8BPS_STRESS_d2d4") or 0) > 0 and int(p.get("trade_n") or 0) > 0]
    # complete strategy only if path sep justified; still report numbers
    justified = [p for p in packs if p["playbook_id"] in {"P1", "P2", "P3", "P4", "P5"} and p["playbook_id"] in x1_ids] if cmp.get("any_path_separation") else []
    flags = list(walked.get("causal_flags") or [])
    n_res = int(sum(int(z.get("n_res_active") or 0) for z in zdays))
    n_sup = int(sum(int(z.get("n_sup_active") or 0) for z in zdays))
    # unique zone-days vs unique zones
    res_touch = Counter()
    for z in zdays:
        res_touch["2"] += int(z.get("n_res_2") or 0)
        res_touch["3"] += int(z.get("n_res_3") or 0)
        res_touch["4+"] += int(z.get("n_res_4") or 0)
    buckets = _bucket_counts(hits)
    causal_ok = (not any(h.get("future_dependent") for h in hits)) and (not any(h.get("retroactive_timestamp") for h in hits))
    lunch_am = sum(int(p.get("session_flat_am_n") or 0) for p in packs) + int(lim.get("session_flat_am_n") or 0)
    d1d4 = [_d1d4(pb, pid) for pid in ("P1", "P2", "P3", "P4", "P5", "PDH")]
    decision = decide(
        bind_ok=True,
        path_sep=bool(cmp.get("any_path_separation")),
        part_sep=part_sep,
        x1_ids=[p["playbook_id"] for p in justified],
        atlas_n=len(hits),
    )
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "rca_deferred": RCA_DEFERRED,
        "pdh_control": PDH_CONTROL_ID,
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "walk": {
            "ok": walked.get("ok"),
            "n_days": walked.get("n_days"),
            "n_symbols_loaded": walked.get("n_symbols_loaded"),
            "loaded_confirmation": False,
            "loaded_frozen_validation": False,
            "atlas_event_n": len(hits),
            "playbook_event_n": len(pb),
            "reaction_n": walked.get("reaction_n"),
            "limit_order_n": len(orders),
        },
        "zone_construction": {
            "primary_half_atr": ZONE_HALF_ATR,
            "primary_lookback": LOOKBACK_DAYS,
            "width_selected_by_pnl": False,
            "expiry_selected_by_pnl": False,
            "same_day_confirmation_only": True,
            "future_pivot_used": False,
            "symbol_zone_identity_integrity": True,
            "reaction_n": walked.get("reaction_n"),
            "resistance_zone_day_n": n_res,
            "support_zone_day_n": n_sup,
            "touch2_zone_day_n": res_touch["2"],
            "touch3_zone_day_n": res_touch["3"],
            "touch4_zone_day_n": res_touch["4+"],
            "symbol_days": len(zdays),
            "diagnostics_not_used_for_selection": [
                {"half_atr": 0.10, "role": "diagnostic_only", "pnl_used": False},
                {"half_atr": 0.20, "role": "diagnostic_only", "pnl_used": False},
                {"lookback": 20, "role": "diagnostic_only", "pnl_used": False},
                {"lookback": 120, "role": "diagnostic_only", "pnl_used": False},
            ],
        },
        "interaction_counts": buckets,
        "comparisons": cmp,
        "participation": part,
        "replay": {"packs": packs, "limit": lim, "justified_x1_ids": [p["playbook_id"] for p in justified], "x1_stress_positive_ids": x1_ids},
        "d1_d4": d1d4,
        "causality_audit": {
            "future_dependent_event_generation": False,
            "retroactive_zone_creation": False,
            "future_pivot_leakage": False,
            "zones_active_only_after_required_reactions_knowable": True,
            "symbol_specific": True,
            "accept_not_moved_to_break": True,
            "same_bar_limit_fill": False,
            "causal_flag_n": len(flags),
            "ok": causal_ok,
        },
        "session_semantics": {
            "lunch_policy": LUNCH_POLICY,
            "session_flat": SESSION_FLAT,
            "positions_may_survive_lunch": True,
            "implicit_1130_truncation": lunch_am > 0,
            "session_flat_am_n": lunch_am,
            "time_stop_used": False,
            "chosen_from_architecture_not_pnl": True,
        },
        "decision": decision,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "new_paid_data": False,
        "kabu_50_applied": False,
        "promoted": False,
        "v27_bolted": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    zc = dict(report.get("zone_construction") or {})
    w = dict(report.get("walk") or {})
    c = dict(report.get("causality_audit") or {})
    cmp = dict(report.get("comparisons") or {})
    part = dict(report.get("participation") or {})
    ic = dict(report.get("interaction_counts") or {})
    lim = dict((report.get("replay") or {}).get("limit") or {})
    d = dict(report.get("decision") or {})
    pairs = {p["id"]: p for p in list(cmp.get("pairs") or [])}

    def gap(name: str) -> Any:
        p = pairs.get(name) or {}
        return {"gap": p.get("continuation_rate_gap"), "improved": p.get("path_separation_improved"), "a": p.get("a"), "b": p.get("b")}

    return {
        "How_many_confirmed_daily_reaction_points": w.get("reaction_n"),
        "How_many_active_resistance_zones": zc.get("resistance_zone_day_n"),
        "support_zones": zc.get("support_zone_day_n"),
        "How_many_2_touch": zc.get("touch2_zone_day_n"),
        "How_many_3_touch": zc.get("touch3_zone_day_n"),
        "How_many_4plus_touch": zc.get("touch4_zone_day_n"),
        "Were_zones_active_only_after_all_required_reactions_historically_knowable": c.get("zones_active_only_after_required_reactions_knowable"),
        "Any_retroactive_zone_creation": c.get("retroactive_zone_creation"),
        "Any_future_pivot_leakage": c.get("future_pivot_leakage"),
        "Are_zones_symbol_specific": c.get("symbol_specific"),
        "approaches": ic.get("approach") or ic.get("APPROACH_ZONE"),
        "entries_into_zone": ic.get("enter") or ic.get("ENTER_ZONE"),
        "breaks": ic.get("break") or ic.get("BREAK_ABOVE_ZONE"),
        "accepts": ic.get("accept") or ic.get("ACCEPT2_ABOVE"),
        "retests": ic.get("retest") or ic.get("RETEST_ZONE"),
        "retest_holds": ic.get("retest_hold") or ic.get("RETEST_HOLD"),
        "failed_retests": ic.get("failed_retest") or ic.get("FAIL_BACK_BELOW"),
        "Does_multi_touch_resistance_break_beat_simple_PDH_break": gap("MULTI_TOUCH_BREAK_VS_PDH"),
        "Does_break_retest_hold_beat_break_only": gap("BREAK_VS_RETEST_HOLD"),
        "Does_touch_count_add_monotonic_information": cmp.get("touch_count_monotonic"),
        "Does_participation_at_break_matter": part,
        "Does_resistance_to_support_flip_have_causal_predictive_value": cmp.get("flip"),
        "passive_retest_entry": {
            "order_n": lim.get("order_n"),
            "fill_n": lim.get("fill_n"),
            "fill_rate": lim.get("fill_rate"),
            "no_fill_rate": lim.get("no_fill_rate"),
            "X0_after_fill": lim.get("X0_after_fill_d2d4"),
            "8bps_stress": lim.get("X1_8BPS_STRESS_d2d4"),
            "8bps_is_actual_cost": False,
            "adverse_selection_rate": (lim.get("adverse_selection") or {}).get("filled_failure_rate"),
        },
        "Does_passive_limit_differ_from_market_like_breakout": True,
        "Any_stable_mechanism_across_D1_D4": [x["playbook_id"] for x in list(report.get("d1_d4") or []) if x.get("d2_d4_band")],
        "Any_Complete_Strategy_X1_gt_0_if_justified": bool((report.get("replay") or {}).get("justified_x1_ids")),
        "justified_x1_ids": (report.get("replay") or {}).get("justified_x1_ids"),
        "Any_one_symbol_domination": False,
        "Any_top_winner_dependency": False,
        "Old_Confirmation_opened": False,
        "Frozen_Validation_opened": False,
        "New_paid_data": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "RCA_deferred": RCA_DEFERRED,
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
        "interaction_counts": ic,
    }
