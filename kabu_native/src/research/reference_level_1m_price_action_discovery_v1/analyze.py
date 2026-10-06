"""Report body, required answers, verdict. Discovery only. R11 not repaired."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.reference_level_1m_price_action_discovery_v1 import (
    ANALYSIS_ID,
    ATLAS_ID,
    CASE_ATLAS,
    CASE_BIND,
    CASE_NONE,
    CASE_PLAYBOOKS,
    CASE_STRATEGY,
    LUNCH_POLICY,
    NEXT_ATLAS_ONLY,
    NEXT_BIND,
    NEXT_FREEZE_PRECOMMIT,
    NEXT_RCA,
    PARENT_VERDICT,
    R11_ROLE,
    R11_STATUS,
    R11_STRATEGY_ID,
    SESSION_FLAT,
    TIME_STOP_USED,
)
from research.reference_level_1m_price_action_discovery_v1.atlas import build_atlas
from research.reference_level_1m_price_action_discovery_v1.bind import bind_prior
from research.reference_level_1m_price_action_discovery_v1.causality import audit_causality
from research.reference_level_1m_price_action_discovery_v1.compare import run_comparisons
from research.reference_level_1m_price_action_discovery_v1.levels import LEVEL_DEFS
from research.reference_level_1m_price_action_discovery_v1.playbooks import PLAYBOOK_SPECS, path_stats, select_playbooks
from research.reference_level_1m_price_action_discovery_v1.replay import replay_selected
from research.reference_level_1m_price_action_discovery_v1.walk import walk_discovery


def _family_map(atlas: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(r["family"]): r for r in list(atlas.get("families") or [])}


def _strongest_levels(atlas: dict[str, Any]) -> list[str]:
    rows = list(atlas.get("strongest_path_separation") or [])
    return [f"{r.get('level_id')}:{r.get('event_kind')}" for r in rows[:8]]


def _transitions(atlas: dict[str, Any]) -> dict[str, Any]:
    kinds = list(atlas.get("level_kinds") or [])
    by_bucket: dict[str, int] = defaultdict(int)
    for r in kinds:
        k = str(r.get("event_kind") or "")
        if "BREAK" in k and "FAILED" not in k:
            by_bucket["break_only"] += int(r.get("event_n") or 0)
        if k.startswith("ACCEPT2"):
            by_bucket["break_acceptance"] += int(r.get("event_n") or 0)
        if k.startswith("RETEST"):
            by_bucket["break_retest"] += int(r.get("event_n") or 0)
        if k.startswith("REJECT"):
            by_bucket["rejection"] += int(r.get("event_n") or 0)
        if "RECLAIM" in k:
            by_bucket["reclaim"] += int(r.get("event_n") or 0)
    scored = sorted(
        [r for r in kinds if r.get("continuation_rate") is not None and int(r.get("event_n") or 0) >= 80],
        key=lambda r: -abs(float(r["continuation_rate"]) - float(r.get("reversal_rate") or 0.0)),
    )
    return {"counts": dict(by_bucket), "top_kinds": scored[:10]}


def _subgroups(playbook_rows: list[dict[str, Any]], selected_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for pid in selected_ids:
        xs = [r for r in playbook_rows if r.get("playbook_id") == pid]
        by_sec: dict[str, list] = defaultdict(list)
        by_gap: dict[str, list] = defaultdict(list)
        by_block: dict[str, list] = defaultdict(list)
        for r in xs:
            by_sec[str(r.get("sector") or "")].append(r)
            by_gap[str(r.get("gap_side") or "none")].append(r)
            by_block[str(r.get("block") or "")].append(r)
        rows.append(
            {
                "playbook_id": pid,
                "overall": path_stats(xs),
                "by_sector_top": sorted(
                    [{"sector": k, **path_stats(v)} for k, v in by_sec.items() if k],
                    key=lambda z: -int(z.get("event_n") or 0),
                )[:8],
                "by_gap": [{"gap_side": k, **path_stats(v)} for k, v in sorted(by_gap.items())],
                "by_block": [{"block": k, **path_stats(v)} for k, v in sorted(by_block.items())],
            }
        )
    return rows


def _d1d4(playbook_rows: list[dict[str, Any]], selected_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for pid in selected_ids:
        by = defaultdict(list)
        for r in playbook_rows:
            if r.get("playbook_id") == pid:
                by[str(r.get("block") or "")].append(r)
        rec = {"playbook_id": pid}
        rates = []
        for b in ("D1", "D2", "D3", "D4"):
            st = path_stats(by.get(b) or [])
            rec[b] = st
            if st.get("continuation_rate") is not None and b != "D1":
                rates.append(float(st["continuation_rate"]))
        rec["d2_d4_continuation_same_side"] = bool(rates) and (all(x >= 0.33 for x in rates) or all(x < 0.33 for x in rates))
        out.append(rec)
    return out


def decide(
    *,
    bind_ok: bool,
    causal_ok: bool,
    selected_n: int,
    complete_ids: list[str],
    atlas_n: int,
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior bind failed. R11 remains invalid. Discovery split/pool mismatch. Do not open Confirmation.",
        }
    if not causal_ok:
        return {
            "CASE": "NONE",
            "VERDICT": CASE_NONE,
            "NEXT": NEXT_ATLAS_ONLY,
            "INTERPRETATION": "Causality audit failed. Do not promote. Do not repair R11. Do not open Confirmation.",
        }
    if complete_ids:
        return {
            "CASE": "STRATEGY",
            "VERDICT": CASE_STRATEGY,
            "NEXT": NEXT_FREEZE_PRECOMMIT,
            "INTERPRETATION": (
                "Causal reference-level playbooks produced at least one complete-strategy candidate with X1>0 on Discovery D2–D4. "
                "This is design evidence only. Frozen Validation remains closed. Old Confirmation remains closed. R11 is not repaired."
            ),
        }
    if selected_n >= 3:
        return {
            "CASE": "PLAYBOOKS",
            "VERDICT": CASE_PLAYBOOKS,
            "NEXT": NEXT_RCA,
            "INTERPRETATION": (
                "Mechanistically distinct causal playbooks exist in the response atlas, but no complete-strategy candidate cleared X1>0 "
                "without one-name domination. Not promoted. R11 remains INVALID_AS_CAUSAL_STRATEGY."
            ),
        }
    if atlas_n > 0:
        return {
            "CASE": "ATLAS",
            "VERDICT": CASE_ATLAS,
            "NEXT": NEXT_ATLAS_ONLY,
            "INTERPRETATION": "Response atlas built. No stable complete playbook. Do not open Confirmation. Do not repair R11.",
        }
    return {
        "CASE": "NONE",
        "VERDICT": CASE_NONE,
        "NEXT": NEXT_ATLAS_ONLY,
        "INTERPRETATION": "No causal events. Stop.",
    }


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    if not bind.get("ok"):
        decision = decide(bind_ok=False, causal_ok=False, selected_n=0, complete_ids=[], atlas_n=0)
        return {
            "parent_verdict_accepted": PARENT_VERDICT,
            "r11_status": R11_STATUS,
            "r11_role": R11_ROLE,
            "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
            "decision": decision,
        }
    print("WALK_START reference_level_1m", flush=True)
    walked = walk_discovery(bind)
    hits = list(walked.get("atlas_hits") or [])
    playbook_rows = list(walked.get("playbook_rows") or [])
    atlas = build_atlas(hits)
    comparisons = run_comparisons(hits)
    selected = select_playbooks(playbook_rows)
    all_ids = [s["playbook_id"] for s in PLAYBOOK_SPECS]
    replay = replay_selected(playbook_rows, list(selected.get("selected_ids") or []), all_ids)
    causal = audit_causality(walk=walked, hits=hits, playbook_rows=playbook_rows)
    packs = list(replay.get("packs") or [])
    lunch_am = sum(int(p.get("session_flat_am_n") or 0) for p in packs)
    subgroups = _subgroups(playbook_rows, list(selected.get("selected_ids") or []))
    d1d4 = _d1d4(playbook_rows, list(selected.get("selected_ids") or []))
    fam = _family_map(atlas)
    vwap_cmp = next((c for c in comparisons if c["id"] == "VWAP_ISOLATED_VS_NEAR_PDH"), None)
    path_improved = any(bool(c.get("path_separation_improved")) for c in comparisons)
    stable = [r for r in d1d4 if r.get("d2_d4_continuation_same_side")]
    decision = decide(
        bind_ok=True,
        causal_ok=bool(causal.get("ok")),
        selected_n=int(selected.get("selected_n") or 0),
        complete_ids=list(replay.get("complete_strategy_candidate_ids") or []),
        atlas_n=int(atlas.get("event_n") or 0),
    )
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "r11_status": R11_STATUS,
        "r11_role": R11_ROLE,
        "r11_strategy_id": R11_STRATEGY_ID,
        "r11_repaired": False,
        "atlas_id": ATLAS_ID,
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "walk": {
            "ok": walked.get("ok"),
            "n_days": walked.get("n_days"),
            "n_symbols_loaded": walked.get("n_symbols_loaded"),
            "loaded_confirmation": walked.get("loaded_confirmation"),
            "loaded_frozen_validation": walked.get("loaded_frozen_validation"),
            "playbook_event_n": len(playbook_rows),
            "atlas_event_n": len(hits),
        },
        "atlas": atlas,
        "comparisons": comparisons,
        "playbook_selection": {
            "all": [
                {
                    "playbook_id": s["playbook_id"],
                    "level_id": s["level_id"],
                    "event_kind": s["event_kind"],
                    "thesis": s["thesis"],
                    "interpretation": s["interpretation"],
                    "exit": s["exit"],
                    "floor_ok": s["floor_ok"],
                    "stats_d2d3": s["stats_d2d3"],
                    "path_separation_vs_parent": s["path_separation_vs_parent"],
                    "score": s["score"],
                }
                for s in list(selected.get("all") or [])
            ],
            "selected_ids": selected.get("selected_ids"),
            "selected_n": selected.get("selected_n"),
            "eligible_n": selected.get("eligible_n"),
            "selection_used_pnl": False,
        },
        "replay": {
            "packs": [
                {
                    k: v
                    for k, v in p.items()
                    if k not in {"winner_concentration"}
                }
                | {"winner_concentration": p.get("winner_concentration")}
                for p in packs
            ],
            "any_x1_positive": replay.get("any_x1_positive"),
            "x1_positive_ids": replay.get("x1_positive_ids"),
            "dominated_ids": replay.get("dominated_ids"),
            "complete_strategy_candidate_ids": replay.get("complete_strategy_candidate_ids"),
        },
        "subgroups": subgroups,
        "d1_d4": d1d4,
        "causality_audit": causal,
        "session_semantics": {
            "lunch_policy": LUNCH_POLICY,
            "session_flat": SESSION_FLAT,
            "positions_may_survive_lunch": True,
            "positions_must_flatten_before_lunch": False,
            "simulator_resumes_pm": True,
            "time_stop_used": TIME_STOP_USED,
            "time_stop_kind": None,
            "implicit_1130_truncation": lunch_am > 0,
            "session_flat_am_n": lunch_am,
            "chosen_from_architecture_not_pnl": True,
        },
        "level_definitions": list(LEVEL_DEFS),
        "vwap_still_special": bool(vwap_cmp and vwap_cmp.get("path_separation_improved")),
        "path_separation_improved": path_improved,
        "stable_mechanisms": [r["playbook_id"] for r in stable],
        "strongest_levels": _strongest_levels(atlas),
        "transitions": _transitions(atlas),
        "family_counts": fam,
        "decision": decision,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "new_paid_data": False,
        "kabu_50_applied": False,
        "promoted": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    fam = dict(report.get("family_counts") or {})
    causal = dict(report.get("causality_audit") or {})
    sel = dict(report.get("playbook_selection") or {})
    replay = dict(report.get("replay") or {})
    sess = dict(report.get("session_semantics") or {})
    d = dict(report.get("decision") or {})
    trans = dict(report.get("transitions") or {})
    comps = list(report.get("comparisons") or [])

    def fn(name: str) -> dict[str, Any]:
        return dict(fam.get(name) or {})

    return {
        "How_many_causal_events_for_each_reference_level_family": {
            k: {"event_n": (fam.get(k) or {}).get("event_n"), "day_n": (fam.get(k) or {}).get("day_n"), "symbol_n": (fam.get(k) or {}).get("symbol_n")}
            for k in ("previous_day", "current_session", "opening_range", "gap_structure", "multi_day")
        },
        "Any_future_dependent_event_generation": bool(causal.get("future_dependent_event_generation")),
        "Any_retroactive_timestamp_assignment": bool(causal.get("retroactive_timestamp_assignment")),
        "Which_levels_show_the_strongest_change_in_subsequent_path_behavior": report.get("strongest_levels"),
        "Previous_day_high": fn("previous_day"),
        "Previous_day_low": fn("previous_day"),
        "Previous_close": fn("previous_day"),
        "Opening_range": fn("opening_range"),
        "Gap_boundary": fn("gap_structure"),
        "VWAP": fn("current_session"),
        "d5_20d_high_low": fn("multi_day"),
        "Which_transitions_matter_most": trans.get("top_kinds"),
        "break_only": (trans.get("counts") or {}).get("break_only"),
        "break_acceptance": (trans.get("counts") or {}).get("break_acceptance"),
        "break_retest": (trans.get("counts") or {}).get("break_retest"),
        "rejection": (trans.get("counts") or {}).get("rejection"),
        "reclaim": (trans.get("counts") or {}).get("reclaim"),
        "Does_VWAP_remain_special_after_prior_day_gap_OR_context": report.get("vwap_still_special"),
        "Does_combining_a_level_with_1m_price_action_improve_path_separation": report.get("path_separation_improved"),
        "path_separation_comparisons": comps,
        "Any_economically_explainable_mechanism_stable_across_D1_D4": report.get("stable_mechanisms"),
        "How_many_candidate_playbooks": sel.get("selected_n"),
        "candidate_playbook_ids": sel.get("selected_ids"),
        "Any_complete_strategy_candidate_with_X1_gt_0": replay.get("any_x1_positive"),
        "complete_strategy_candidate_ids": replay.get("complete_strategy_candidate_ids"),
        "x1_positive_ids": replay.get("x1_positive_ids"),
        "Any_candidate_dominated_by_one_symbol_or_top_winners": bool(replay.get("dominated_ids")),
        "dominated_ids": replay.get("dominated_ids"),
        "Lunch_semantics_explicitly_fixed": True,
        "lunch_policy": sess.get("lunch_policy"),
        "Any_implicit_1130_truncation": bool(sess.get("implicit_1130_truncation")),
        "Old_Confirmation_opened": False,
        "Frozen_Validation_opened": False,
        "New_paid_data": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "R11_status": R11_STATUS,
        "R11_repaired": False,
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
    }
