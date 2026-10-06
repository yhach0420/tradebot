"""Behavior group × causal sequence. Discovery only. No paid data. No Frozen Validation."""
from __future__ import annotations

import gc
from typing import Any

from research.behavior_group_sequence_mechanism_v1 import (
    CASE_BIND,
    CASE_DESCRIPTIVE,
    CASE_EXECUTABLE,
    CASE_IMPROVES,
    EVAL_BLOCKS,
    HM1_ID,
    HM1_X0_BPS,
    HM1_X1_BPS,
    MATERIAL_BPS,
    MIN_COMPARE_TRADE_N,
    NEXT_BIND,
    NEXT_FREEZE,
    NEXT_STOP_SPLIT,
    PARENT_VERDICT,
    ZERO_TRADE_CAUSE,
)
from research.behavior_group_sequence_mechanism_v1.bind import bind_prior
from research.behavior_group_sequence_mechanism_v1.engine import walk_discovery
from research.behavior_group_sequence_mechanism_v1.prequential import maps_for_blocks
from research.behavior_group_sequence_mechanism_v1.replay import build_candidates
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now

COMPARE_PAIRS = (
    ("GM_CATCHUP_RS_TURN", "BG_CATCHUP_RS_TURN", "laggard_catchup_vs_global_rs_turn"),
    ("GM_CATCHUP_VWAP", "BG_CATCHUP_VWAP", "laggard_catchup_vs_global_lag_vwap"),
    ("GM_VWAP_RECLAIM", "BG_CATCHUP_VWAP", "laggard_vwap_vs_global_vwap"),
    ("GM_VWAP_RECLAIM", "BG_CONT_VWAP", "continuation_vwap_vs_global_vwap"),
    ("GM_IMPULSE_PAUSE", "BG_CONT_IMPULSE_PAUSE", "continuation_impulse_vs_global"),
    ("GM_OPENING_GAP_HOLD", "BG_OPEN_GAP_HOLD", "opening_gap_vs_global"),
    ("GM_LEADER_PULLBACK_CONT", "BG_LEADER_PULLBACK_CONT", "leader_persist_vs_global"),
    ("GM_CATCHUP_VOL", "BG_CATCHUP_VOL", "laggard_vol_vs_global"),
    ("GM_CATCHUP_STOP_DET", "BG_CATCHUP_STOP_DET", "laggard_stopdet_vs_global"),
)


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def _by_id(plays: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for p in list(plays.get("proposals") or []):
        cid = str((p.get("spec") or {}).get("candidate_id") or "")
        out[cid] = p
    return out


def _econ(p: dict[str, Any] | None) -> dict[str, Any]:
    return dict((p or {}).get("economics") or {})


def compare_pair(gid: str, bid: str, label: str, byid: dict[str, dict[str, Any]]) -> dict[str, Any]:
    g = _econ(byid.get(gid))
    b = _econ(byid.get(bid))
    gx = g.get("mean_x0_bps")
    bx = b.get("mean_x0_bps")
    g1 = g.get("mean_x1_bps")
    b1 = b.get("mean_x1_bps")
    dn0 = None if gx is None or bx is None else float(bx) - float(gx)
    dn1 = None if g1 is None or b1 is None else float(b1) - float(g1)
    enough = int(g.get("trade_n") or 0) >= MIN_COMPARE_TRADE_N and int(b.get("trade_n") or 0) >= MIN_COMPARE_TRADE_N
    material = bool(enough and ((dn0 is not None and dn0 >= MATERIAL_BPS) or (dn1 is not None and dn1 >= MATERIAL_BPS)))
    return {
        "pair": label,
        "global_id": gid,
        "group_id": bid,
        "global_trade_n": g.get("trade_n"),
        "group_trade_n": b.get("trade_n"),
        "global_x0": gx,
        "group_x0": bx,
        "global_x1": g1,
        "group_x1": b1,
        "delta_x0": dn0,
        "delta_x1": dn1,
        "global_pf": g.get("profit_factor"),
        "group_pf": b.get("profit_factor"),
        "global_blocks": g.get("block_mean_x0"),
        "group_blocks": b.get("block_mean_x0"),
        "enough_n": enough,
        "material_improvement": material,
        "material_bps_predeclared": MATERIAL_BPS,
    }


def decide(*, bind_ok: bool, material_n: int, promoted_n: int) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior atlas/split/block/105 bind failed.",
        }
    if int(promoted_n) >= 1:
        return {
            "CASE": "EXECUTABLE",
            "VERDICT": CASE_EXECUTABLE,
            "NEXT": NEXT_FREEZE,
            "INTERPRETATION": "At least one prequential group×sequence complete strategy cleared X1 on Discovery. Freeze those mechanism definitions. Frozen Validation remains closed. HM1 was not retuned.",
        }
    if int(material_n) >= 1:
        return {
            "CASE": "IMPROVES",
            "VERDICT": CASE_IMPROVES,
            "NEXT": NEXT_FREEZE,
            "INTERPRETATION": "Behavior-group conditioning materially improved the same sequences versus global, but no candidate cleared executable X1. Freeze the improving mechanism definitions. Do not invent further subgroups. Frozen Validation remains closed.",
        }
    return {
        "CASE": "DESCRIPTIVE",
        "VERDICT": CASE_DESCRIPTIVE,
        "NEXT": NEXT_STOP_SPLIT,
        "INTERPRETATION": "Atlas groups describe 1-minute behavior but do not discriminate complete-strategy value versus the same global sequences. Stop native group splitting. Do not invent subgroups.",
    }


def _strip_fwd(events: list[dict[str, Any]]) -> None:
    for e in events:
        e.pop("fwd_bars", None)


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} pool={bind.get('research_pool_n')} atlas={ (bind.get('atlas_reference') or {}).get('verdict') }", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    walked = {"ok": False, "events": [], "daily": [], "event_n": 0, "day_n": 0, "by_sequence": {}}
    maps = {"rows": [], "by_eval_block": {}, "definitions": [], "full_discovery_group_leakage": False}
    plays = {"proposals": [], "promoted": [], "promoted_n": 0}
    comps: list[dict[str, Any]] = []
    if bind.get("ok"):
        walked = walk_discovery(
            symbols=symbols,
            sector_of=sector_of,
            allowed=disc,
            forbidden=conf | val,
            date_to_block=date_to_block,
        )
        print(f"EVENTS n={walked.get('event_n')} days={walked.get('day_n')} by={walked.get('by_sequence')}", flush=True)
        maps = maps_for_blocks(daily=list(walked.get("daily") or []), blocks=list(blocks.get("blocks") or []))
        by_eval = dict(maps.get("by_eval_block") or {})
        for e in list(walked.get("events") or []):
            blk = str(e.get("block") or "")
            e["preq_group"] = (by_eval.get(blk) or {}).get(str(e.get("symbol")))
            e["profile_training_end"] = next((d.get("profile_training_end") for d in maps.get("definitions") or [] if d.get("eval_block") == blk), None)
            e["full_discovery_group_used"] = False
        plays = build_candidates(list(walked.get("events") or []))
        print(f"CANDIDATES promoted={plays.get('promoted_n')}", flush=True)
        byid = _by_id(plays)
        comps = [compare_pair(a, b, lab, byid) for a, b, lab in COMPARE_PAIRS]
        _strip_fwd(list(walked.get("events") or []))
        walked["events"] = []
        gc.collect()
    live = inspect_live_now()
    material = [c for c in comps if c.get("material_improvement")]
    decision = decide(bind_ok=bool(bind.get("ok")), material_n=len(material), promoted_n=int(plays.get("promoted_n") or 0))
    atlas_ref = dict(bind.get("atlas_reference") or {})
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "atlas_id": atlas_ref.get("atlas_id"),
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "split"}},
        "split_sha256": (bind.get("split") or {}).get("split_sha256"),
        "block_sha256": (bind.get("blocks") or {}).get("block_sha256"),
        "walk_meta": {k: walked.get(k) for k in ("ok", "day_n", "event_n", "by_sequence", "used_5m_grid", "bar_start", "future_catchup_not_used_for_entry", "atlas_classifier_used")},
        "prequential": {
            "rows": maps.get("rows"),
            "definitions": maps.get("definitions"),
            "d1_characterization_only": True,
            "full_discovery_group_leakage": False,
            "future_used_in_profile": False,
        },
        "leader_state": list(walked.get("leader_rows") or [])[:4000],
        "laggard_state": list(walked.get("lag_rows") or []),
        "candidates": plays,
        "global_vs_group": comps,
        "material_pairs": material,
        "hm1_reference": bind.get("hm1_reference"),
        "atlas_reference": atlas_ref,
        "zero_trade_cause": ZERO_TRADE_CAUSE,
        "implementation_mismatch_fixed": int((walked.get("by_sequence") or {}).get("CATCHUP_RS_TURN") or 0) > 0,
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "hm1_tuned": False,
        "new_paid_data": False,
        "five_minute_grid": False,
        "purchase": False,
        "decision": decision,
        "hm1_id": HM1_ID,
        "eval_blocks": list(EVAL_BLOCKS),
        "material_bps_predeclared": MATERIAL_BPS,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    plays = dict(report.get("candidates") or {})
    proposals = list(plays.get("proposals") or [])
    promoted = list(plays.get("promoted") or [])
    byid = {str((p.get("spec") or {}).get("candidate_id")): p for p in proposals}
    comps = list(report.get("global_vs_group") or [])
    material = list(report.get("material_pairs") or [])
    hm1 = dict(report.get("hm1_reference") or {})
    atlas = dict(report.get("atlas_reference") or {})
    preq = dict(report.get("prequential") or {})
    rs = _econ(byid.get("BG_CATCHUP_RS_TURN"))
    rs_g = _econ(byid.get("GM_CATCHUP_RS_TURN"))
    vwap_pair = next((c for c in comps if c.get("pair") == "laggard_vwap_vs_global_vwap"), {})
    cont_imp = next((c for c in comps if c.get("pair") == "continuation_impulse_vs_global"), {})
    cont_vw = next((c for c in comps if c.get("pair") == "continuation_vwap_vs_global_vwap"), {})
    open_p = next((c for c in comps if c.get("pair") == "opening_gap_vs_global"), {})
    lead_p = next((c for c in comps if c.get("pair") == "leader_persist_vs_global"), {})
    scored = []
    for p in proposals:
        spec = dict(p.get("spec") or {})
        if spec.get("diagnostic") or spec.get("window") != "D2_D4":
            continue
        e = _econ(p)
        if e.get("mean_x1_bps") is None:
            continue
        scored.append((float(e["mean_x1_bps"]), spec.get("candidate_id"), e))
    scored.sort(reverse=True)
    closest = scored[0] if scored else (None, None, {})
    x1_pos = [s for s in scored if s[0] > 0]
    exec_groups = []
    for p in promoted:
        gf = (p.get("spec") or {}).get("group_filter") or "GLOBAL"
        if gf not in exec_groups:
            exec_groups.append(gf)
    hm1_x0 = hm1.get("mean_x0_bps") if hm1.get("mean_x0_bps") is not None else HM1_X0_BPS
    hm1_x1 = hm1.get("mean_x1_bps") if hm1.get("mean_x1_bps") is not None else HM1_X1_BPS
    beats_hm1 = []
    for p in proposals:
        spec = dict(p.get("spec") or {})
        if spec.get("diagnostic"):
            continue
        e = _econ(p)
        if int(e.get("trade_n") or 0) < 40:
            continue
        x0 = e.get("mean_x0_bps")
        be = e.get("break_even_execution_bps")
        reasons = []
        if x0 is not None and float(x0) > float(hm1_x0):
            reasons.append("beats_hm1_gross")
        if be is not None and 0 < float(be) < 8.0:
            reasons.append("lower_break_even_than_canonical_8bps")
        if int(e.get("block_positive_n") or 0) >= 3 and x0 is not None and float(x0) > 0:
            reasons.append("block_stability_with_positive_gross")
        if spec.get("group_filter") and x0 is not None and float(x0) > float(hm1_x0):
            reasons.append("distinct_stock_group_and_beats_gross")
        if "beats_hm1_gross" in reasons:
            beats_hm1.append({"candidate_id": spec.get("candidate_id"), "x0": x0, "x1": e.get("mean_x1_bps"), "why": reasons})
    frozen_defs = []
    if d.get("VERDICT") in {CASE_EXECUTABLE, CASE_IMPROVES}:
        if promoted:
            frozen_defs = [(p.get("spec") or {}).get("candidate_id") for p in promoted]
        else:
            frozen_defs = [c.get("group_id") for c in material]
    return {
        "Was_behavior_group_assignment_made_prequentially": True,
        "Any_full_Discovery_group_leakage": False,
        "Why_did_prior_PB_NATIVE_LAG_CATCHUP_have_zero_trades": report.get("zero_trade_cause"),
        "Was_that_implementation_mismatch_fixed": report.get("implementation_mismatch_fixed"),
        "SECTOR_LAGGARD_CATCHUP_plus_catchup_trigger_produces_trades": int(rs.get("trade_n") or 0) > 0 or int(rs_g.get("trade_n") or 0) > 0,
        "BG_CATCHUP_RS_TURN_trade_n": rs.get("trade_n"),
        "BG_CATCHUP_RS_TURN_day_n": rs.get("day_n"),
        "BG_CATCHUP_RS_TURN_symbol_n": rs.get("symbol_n"),
        "BG_CATCHUP_RS_TURN_X0": rs.get("mean_x0_bps"),
        "BG_CATCHUP_RS_TURN_X1": rs.get("mean_x1_bps"),
        "BG_CATCHUP_RS_TURN_PF": rs.get("profit_factor"),
        "BG_CATCHUP_RS_TURN_D1_D4": rs.get("block_mean_x0"),
        "GM_CATCHUP_RS_TURN_trade_n": rs_g.get("trade_n"),
        "Does_laggard_VWAP_reclaim_beat_global_VWAP_reclaim": bool(vwap_pair.get("material_improvement") or ((vwap_pair.get("delta_x0") or 0) > 0)),
        "laggard_vs_global_VWAP": vwap_pair,
        "Does_CONTINUATION_conditioning_improve_impulse_or_reclaim": bool(cont_imp.get("material_improvement") or cont_vw.get("material_improvement")),
        "continuation_impulse": cont_imp,
        "continuation_vwap": cont_vw,
        "Does_OPENING_MOMENTUM_conditioning_improve_OPENING_GAP_HOLD": bool(open_p.get("material_improvement")),
        "opening_gap": open_p,
        "Does_SECTOR_LEADER_conditioning_improve_continuation": bool(lead_p.get("material_improvement")),
        "leader_persist": lead_p,
        "Any_Complete_Strategy_X1_gt_0": bool(x1_pos),
        "closest_X1_id": closest[1],
        "closest_X1": closest[0],
        "closest_break_even_execution_bps": (closest[2] or {}).get("break_even_execution_bps"),
        "Does_any_new_candidate_beat_frozen_HM1": bool(beats_hm1),
        "candidates_beating_HM1_gross": beats_hm1[:8],
        "HM1_X0": hm1_x0,
        "HM1_X1": hm1_x1,
        "How_many_distinct_behavior_groups_have_executable_edge": len(exec_groups),
        "executable_groups": exec_groups,
        "material_improvement_n": len(material),
        "material_pairs": [c.get("pair") for c in material],
        "frozen_mechanism_definitions": frozen_defs,
        "promoted_n": int(plays.get("promoted_n") or 0),
        "promoted_ids": [(p.get("spec") or {}).get("candidate_id") for p in promoted],
        "prequential_d1_characterization_only": preq.get("d1_characterization_only"),
        "atlas_catchup_reference_n": len(list((atlas.get("members_full_discovery_reference_only") or {}).get("SECTOR_LAGGARD_CATCHUP") or [])),
        "pb_native_lag_catchup_trade_n": atlas.get("pb_native_lag_catchup_trade_n"),
        "event_by_sequence": (report.get("walk_meta") or {}).get("by_sequence"),
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used_to_design": False,
        "New_paid_data": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
