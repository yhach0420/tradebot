"""Exit-only E0/E1/E2 on frozen BG_CONT_VWAP. Full event-time replay. Discovery design evidence."""
from __future__ import annotations

import gc
from typing import Any

from research.behavior_group_sequence_mechanism_v1.prequential import maps_for_blocks
from research.bg_cont_vwap_largest_causal_deficiency_v1 import (
    CASE_BIND,
    CASE_E1,
    CASE_E2,
    CASE_NONE,
    CASE_PARITY,
    EXPECTED_MECHANISM_HASH,
    NEXT_BIND,
    NEXT_FREEZE_STRATEGY,
    NEXT_PARITY,
    NEXT_STOP,
    PARENT_VERDICT,
)
from research.bg_cont_vwap_largest_causal_deficiency_v1.bind import bind_prior
from research.bg_cont_vwap_largest_causal_deficiency_v1.evaluate import compare_to_e0, e0_parity, pick_winner
from research.bg_cont_vwap_largest_causal_deficiency_v1.exits import EVENT_ORDER_E0, EVENT_ORDER_E1_E2, EXIT_SPECS, spec_sha
from research.bg_cont_vwap_largest_causal_deficiency_v1.replay import replay_exit
from research.freeze_group_mechanism_definitions_v1.freeze import frozen_spec
from research.freeze_group_mechanism_definitions_v1.walk import walk_vwap_reclaim
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def _strip(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k != "trades"}


def decide(*, bind_ok: bool, parity_ok: bool, winner: str | None) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "RCA/split/block bind failed."}
    if not parity_ok:
        return {
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_PARITY,
            "INTERPRETATION": "E0 did not reproduce 486 / +6.7944756821. Do not interpret E1/E2.",
        }
    if winner == "E2":
        return {
            "CASE": "E2",
            "VERDICT": CASE_E2,
            "NEXT": NEXT_FREEZE_STRATEGY,
            "INTERPRETATION": "E2 MFE8 asymmetric VWAP grace survived as design evidence, not certification. Freeze this EXIT next. Frozen Validation remains closed. Old Confirmation not used to design.",
        }
    if winner == "E1":
        return {
            "CASE": "E1",
            "VERDICT": CASE_E1,
            "NEXT": NEXT_FREEZE_STRATEGY,
            "INTERPRETATION": "E1 two-bar VWAP persist survived as design evidence, not certification. Freeze this EXIT next. Frozen Validation remains closed.",
        }
    return {
        "CASE": "NONE",
        "VERDICT": CASE_NONE,
        "NEXT": NEXT_STOP,
        "INTERPRETATION": "Neither E1 nor E2 materially rescued giveback on the complete chain. Stop refining BG_CONT_VWAP. Do not test 3-bar persistence, new MFE thresholds, symbol exits, or time exits.",
    }


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} rca={bind.get('rca_verdict')}", flush=True)
    parent_spec = frozen_spec()
    exit_specs = []
    for raw in EXIT_SPECS:
        spec = dict(raw)
        spec["spec_sha256"] = spec_sha(spec)
        exit_specs.append(spec)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    packs = {"E0": {"ok": False, "trade_n": 0}, "E1": {"ok": False}, "E2": {"ok": False}}
    parity = {"ok": False}
    comps: dict[str, Any] = {}
    picked = {"survivors": [], "winning_exit": None, "e2_outperforms_e1": False}
    if bind.get("ok"):
        walked = walk_vwap_reclaim(
            symbols=list(bind.get("symbols") or []),
            sector_of=_sector_of(bind),
            allowed=disc,
            forbidden=conf | val,
            date_to_block=date_to_block,
        )
        print(f"VWAP_EVENTS n={walked.get('event_n')}", flush=True)
        maps = maps_for_blocks(daily=list(walked.get("daily") or []), blocks=list(blocks.get("blocks") or []))
        by_eval = dict(maps.get("by_eval_block") or {})
        events = list(walked.get("events") or [])
        for e in events:
            blk = str(e.get("block") or "")
            e["preq_group"] = (by_eval.get(blk) or {}).get(str(e.get("symbol")))
            e["full_discovery_group_used"] = False
        for eid in ("E0", "E1", "E2"):
            packs[eid] = replay_exit(events, parent_spec, exit_id=eid)
            print(f"{eid} n={packs[eid].get('trade_n')} x0={packs[eid].get('mean_x0_bps')} x1={packs[eid].get('mean_x1_bps')}", flush=True)
        parity = e0_parity(packs["E0"])
        print(f"E0_PARITY ok={parity.get('ok')}", flush=True)
        if parity.get("ok"):
            comps["E1"] = compare_to_e0(packs["E1"], packs["E0"])
            comps["E2"] = compare_to_e0(packs["E2"], packs["E0"])
            picked = pick_winner(packs["E0"], packs["E1"], packs["E2"], comps["E1"], comps["E2"])
        events.clear()
        walked["events"] = []
        walked["daily"] = []
        gc.collect()
    live = inspect_live_now()
    decision = decide(bind_ok=bool(bind.get("ok")), parity_ok=bool(parity.get("ok")), winner=picked.get("winning_exit"))
    winning_spec = next((s for s in exit_specs if s["exit_id"] == picked.get("winning_exit")), None)
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "frozen_mechanism_id": "BG_CONT_VWAP",
        "frozen_mechanism_hash": EXPECTED_MECHANISM_HASH,
        "entry_unchanged": True,
        "group_assignment_unchanged": True,
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "split"}},
        "split_sha256": (bind.get("split") or {}).get("split_sha256"),
        "block_sha256": (bind.get("blocks") or {}).get("block_sha256"),
        "parent_spec": parent_spec,
        "exit_specs": exit_specs,
        "event_order_e0": list(EVENT_ORDER_E0),
        "event_order_e1_e2": list(EVENT_ORDER_E1_E2),
        "replay_parity": parity,
        "E0": _strip(packs["E0"]),
        "E1": _strip(packs["E1"]),
        "E2": _strip(packs["E2"]),
        "comparisons": comps,
        "winner": picked,
        "winning_exit_spec": winning_spec,
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "hm1_tuned": False,
        "new_paid_data": False,
        "five_minute_grid": False,
        "promoted": False,
        "v27_bolted": False,
        "entry_changed": False,
        "take_profit_tested": False,
        "d2_d4_not_a_new_holdout": True,
        "design_evidence_not_certification": True,
        "purchase": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    e0 = dict(report.get("E0") or {})
    e1 = dict(report.get("E1") or {})
    e2 = dict(report.get("E2") or {})
    c1 = dict((report.get("comparisons") or {}).get("E1") or {})
    c2 = dict((report.get("comparisons") or {}).get("E2") or {})
    w = dict(report.get("winner") or {})
    p = dict(report.get("replay_parity") or {})
    gb = []
    for eid, pack in (("E0", e0), ("E1", e1), ("E2", e2)):
        g = (pack.get("giveback") or {}).get("average_giveback")
        if g is not None:
            gb.append((float(g), eid))
    gb.sort()
    large = []
    for eid, pack in (("E0", e0), ("E1", e1), ("E2", e2)):
        f = (pack.get("giveback") or {}).get("fraction_large_winner")
        if f is not None:
            large.append((float(f), eid))
    large.sort(reverse=True)
    x1_any = any(float(p.get("mean_x1_bps") or -9) > 0 for p in (e1, e2) if p.get("mean_x1_bps") is not None)
    return {
        "E0_replay_parity_exact": bool(p.get("ok")),
        "E0_trade_n": e0.get("trade_n"),
        "E0_X0": e0.get("mean_x0_bps"),
        "E0_X1": e0.get("mean_x1_bps"),
        "E1_X0": e1.get("mean_x0_bps"),
        "E1_X1": e1.get("mean_x1_bps"),
        "E1_PF": e1.get("profit_factor"),
        "E2_X0": e2.get("mean_x0_bps"),
        "E2_X1": e2.get("mean_x1_bps"),
        "E2_PF": e2.get("profit_factor"),
        "Which_reduced_giveback_most": gb[0][1] if gb else None,
        "giveback_means": {eid: (pack.get("giveback") or {}).get("average_giveback") for eid, pack in (("E0", e0), ("E1", e1), ("E2", e2))},
        "Which_preserved_large_winners": large[0][1] if large else None,
        "large_winner_fracs": {eid: (pack.get("giveback") or {}).get("fraction_large_winner") for eid, pack in (("E0", e0), ("E1", e1), ("E2", e2))},
        "Did_profitable_then_loss_decrease_E1": c1.get("delta_ptl_frac"),
        "Did_profitable_then_loss_decrease_E2": c2.get("delta_ptl_frac"),
        "Did_true_immediate_failures_worsen_E1": c1.get("delta_immediate_failure_path_n"),
        "Did_true_immediate_failures_worsen_E2": c2.get("delta_immediate_failure_path_n"),
        "Did_delayed_exit_increase_capacity_blocking_E1": c1.get("delta_occupancy_skips"),
        "Did_delayed_exit_increase_capacity_blocking_E2": c2.get("delta_occupancy_skips"),
        "Which_symbols_improved_E1": c1.get("symbols_improved"),
        "Which_symbols_improved_E2": c2.get("symbols_improved"),
        "Was_improvement_broad_or_8136_dominated_E1": "broad" if c1.get("E_not_only_8136") else "8136_or_narrow",
        "Was_improvement_broad_or_8136_dominated_E2": "broad" if c2.get("E_not_only_8136") else "8136_or_narrow",
        "E1_D2": (e1.get("block_mean_x0") or {}).get("D2"),
        "E1_D3": (e1.get("block_mean_x0") or {}).get("D3"),
        "E1_D4": (e1.get("block_mean_x0") or {}).get("D4"),
        "E1_D2_D3": (e1.get("d2_d3") or {}).get("mean_x0_bps"),
        "E2_D2": (e2.get("block_mean_x0") or {}).get("D2"),
        "E2_D3": (e2.get("block_mean_x0") or {}).get("D3"),
        "E2_D4": (e2.get("block_mean_x0") or {}).get("D4"),
        "E2_D2_D3": (e2.get("d2_d3") or {}).get("mean_x0_bps"),
        "Any_X1_gt_0": x1_any,
        "Any_candidate_materially_better_than_E0": bool(w.get("survivors")),
        "Does_E2_outperform_E1": w.get("e2_outperforms_e1"),
        "hypothesis_after_continuation_one_bar_is_noise": w.get("hypothesis_after_continuation_one_bar_is_noise"),
        "Winning_EXIT": w.get("winning_exit"),
        "Complete_strategy_promoted": False,
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used_to_design": False,
        "New_paid_data": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "E1_survives": c1.get("survives"),
        "E2_survives": c2.get("survives"),
        "frozen_mechanism_hash": report.get("frozen_mechanism_hash"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
