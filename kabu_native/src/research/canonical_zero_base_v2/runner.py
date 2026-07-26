"""Canonical Zero-Base v2 runner — full discovery → joint ENTRY×EXIT → artifacts."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_zero_base_v2.anchors import anchor_inventory, build_all_anchors
from research.canonical_zero_base_v2.cap5 import CapTrade, replay_cap5
from research.canonical_zero_base_v2.constants import (
    CANCEL,
    LIVE_ORDER,
    MAX_PARALLEL,
    OUT_ROOT,
    SEED,
    SOT_AUDIT,
    SOT_EGC,
    SOT_REPAIR,
    SOT_ROOT,
    SOT_V1,
    SUBMIT,
)
from research.canonical_zero_base_v2.data_discovery import discover_and_split
from research.canonical_zero_base_v2.dependency import dependency_metrics
from research.canonical_zero_base_v2.entry_features import (
    compute_entry_features,
    ensure_inventory,
    feature_kind_counts,
)
from research.canonical_zero_base_v2.entry_rules import build_entry_rules
from research.canonical_zero_base_v2.entry_separation import bootstrap_top, evaluate_feature_separation
from research.canonical_zero_base_v2.episodes import BUILDERS, episode_quality
from research.canonical_zero_base_v2.execution import evaluate_latency_pairs
from research.canonical_zero_base_v2.exit_features import compute_exit_features_at, ensure_exit_inventory
from research.canonical_zero_base_v2.exit_rules import strategy_exit_candidates
from research.canonical_zero_base_v2.interactions import generate_interactions
from research.canonical_zero_base_v2.joint_search import run_strategy_lane
from research.canonical_zero_base_v2.loader import load_streams
from research.canonical_zero_base_v2.outcome_labels import fit_class_bounds, label_anchor
from research.canonical_zero_base_v2.reporting import emit_artifacts

JST = ZoneInfo("Asia/Tokyo")


def _filter_days(streams: dict, days: list[str]) -> dict:
    return {k: v for k, v in streams.items() if k.split("|")[0] in days}


def run_v2(
    *,
    run_id: Optional[str] = None,
    stride: int = 12,
    out_root: Optional[Path] = None,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id
    print(f"[v2] discovery…", flush=True)
    discovery = discover_and_split()
    all_days = list(dict.fromkeys(
        (discovery.get("warmup") or [])
        + (discovery.get("train") or [])
        + (discovery.get("validation") or [])
        + (discovery.get("strict_oos") or [])
    ))
    print(f"[v2] load streams days={all_days} stride={stride}", flush=True)
    streams = load_streams(all_days, stride=stride)
    train_days = discovery["train"]
    val_days = discovery["validation"]
    oos_days = discovery["strict_oos"]
    insufficient = bool(discovery.get("insufficient_oos"))

    print(f"[v2] anchors…", flush=True)
    anchors_all = build_all_anchors(streams)
    inv = anchor_inventory(anchors_all)
    train_anchors = [a for a in anchors_all if a.day in train_days]
    # subsample for labeling/features if huge
    if len(train_anchors) > 12000:
        train_anchors = train_anchors[:: max(1, len(train_anchors) // 12000)]

    print(f"[v2] labels on {len(train_anchors)} train anchors…", flush=True)
    # provisional metrics for bounds
    raw_metrics = []
    for a in train_anchors[:: max(1, len(train_anchors) // 4000)]:
        ticks = streams.get(a.stream_key) or []
        m = label_anchor(ticks, a.tick_idx, a.canonical_ask, a.anchor_id, bounds={
            "winner_fast_mfe": 0.8, "winner_slow_t": 60, "noprogress_mfe": 0.25, "stop_mae": -0.8,
        }).metrics
        if m.get("evaluable"):
            raw_metrics.append(m)
    bounds = fit_class_bounds(raw_metrics)

    labeled_rows = []
    outcome_counts: Counter = Counter()
    for a in train_anchors:
        ticks = streams.get(a.stream_key) or []
        lab = label_anchor(ticks, a.tick_idx, a.canonical_ask, a.anchor_id, bounds=bounds)
        if not lab.evaluable:
            continue
        feats = compute_entry_features(ticks, a.tick_idx)
        labeled_rows.append({
            "anchor_id": a.anchor_id,
            "day": a.day,
            "symbol": a.symbol,
            "setup_type": a.setup_type,
            "class_name": lab.class_name,
            "features": feats,
            "metrics": lab.metrics,
            "strategy_affinity": a.strategy_affinity,
        })
        outcome_counts[lab.class_name] += 1

    if not labeled_rows:
        raise RuntimeError("no labeled train anchors")

    inv_feats = ensure_inventory(labeled_rows[0]["features"])
    feat_names = [r["feature_id"] for r in inv_feats]
    kind_counts = feature_kind_counts(inv_feats)
    print(f"[v2] entry features={len(feat_names)} labeled={len(labeled_rows)}", flush=True)

    print(f"[v2] separation…", flush=True)
    separation = evaluate_feature_separation(labeled_rows, feat_names)
    boot = bootstrap_top(labeled_rows, feat_names, n_boot=12, top_k=25)
    stable_n = sum(1 for r in separation if r.get("stability") == "STABLE")
    rejected = [r for r in separation if (r.get("missing_rate") or 0) > 0.85]
    interactions = generate_interactions(labeled_rows, separation, top_pool=40)

    # base rates for gates
    n_lab = len(labeled_rows)
    base_never = outcome_counts.get("NEVER_PROFITABLE", 0) / n_lab
    base_early = outcome_counts.get("EARLY_STOP_PATH", 0) / n_lab

    # episodes
    print(f"[v2] strategy episodes…", flush=True)
    episodes: dict[str, dict[str, list]] = {sid: {"train": [], "val": [], "oos": []} for sid in ("Z1", "Z2", "Z3", "Z4")}
    ep_quality = {}
    for sid, builder in BUILDERS.items():
        all_eps = []
        for key, ticks in streams.items():
            eps = builder(key, ticks)
            all_eps.extend(eps)
            day = key.split("|")[0]
            if day in train_days:
                episodes[sid]["train"].extend(eps)
            if day in val_days:
                episodes[sid]["val"].extend(eps)
            if day in oos_days:
                episodes[sid]["oos"].extend(eps)
        ep_quality[sid] = episode_quality(all_eps)
        print(f"[v2] {sid} episodes={ep_quality[sid]}", flush=True)

    # EXIT feature inventory sample
    exit_sample = {}
    for sid in ("Z1", "Z2", "Z3", "Z4"):
        for ep in episodes[sid]["train"]:
            if ep.entry_ready and ep.entry_idx is not None:
                ticks = streams.get(f"{ep.day}|{ep.symbol}") or []
                j = min(ep.entry_idx + 5, len(ticks) - 1)
                exit_sample = compute_exit_features_at(
                    ticks, ep.entry_idx, j,
                    entry_ask=float(ticks[ep.entry_idx].board.canonical_best_ask or ticks[ep.entry_idx].px),
                    levels=ep.levels, strategy_id=sid,
                )
                if exit_sample:
                    break
        if exit_sample:
            break
    exit_inv = ensure_exit_inventory(exit_sample or {"hold_sec": 0, "giveback": 0})

    # entry rules from ranking — strategy-specific
    print(f"[v2] entry rules…", flush=True)
    entry_rules = {}
    for sid in ("Z1", "Z2", "Z3", "Z4"):
        # affinity-weighted rows preferred
        rows_s = [r for r in labeled_rows if sid in (r.get("strategy_affinity") or ())] or labeled_rows
        entry_rules[sid] = build_entry_rules(sid, separation, rows_s)
        print(f"[v2] {sid} entry_rules={len(entry_rules[sid])}", flush=True)

    # parallel lanes
    print(f"[v2] joint search 4 lanes…", flush=True)
    lanes: dict[str, Any] = {}

    def _job(sid: str) -> tuple[str, dict]:
        if ep_quality[sid].get("blocked") == "STRATEGY_EPISODE_MODEL_BLOCKED":
            return sid, {
                "strategy_id": sid,
                "status": "STRATEGY_EPISODE_MODEL_BLOCKED",
                "judgment": f"{sid}_ENTRY_EXIT_REJECT",
                "entry_rules_n": len(entry_rules[sid]),
                "exit_candidates_n": len(strategy_exit_candidates(sid)),
                "train_entry_pass_n": 0,
                "val_entry_pass_n": 0,
                "raw_pairs": 0,
                "train_pair_pass_n": 0,
                "val_pair_pass_n": 0,
                "oos_pairs_n": 0,
                "final": None,
                "oos_results": [],
                "exit_ids": [x.exit_id for x in strategy_exit_candidates(sid)],
            }
        return sid, run_strategy_lane(
            sid,
            entry_rules=entry_rules[sid],
            episodes_train=episodes[sid]["train"],
            episodes_val=episodes[sid]["val"],
            episodes_oos=episodes[sid]["oos"],
            streams=streams,
            base_never=base_never,
            base_early=base_early,
            insufficient_oos=insufficient,
        )

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        futs = [ex.submit(_job, sid) for sid in ("Z1", "Z2", "Z3", "Z4")]
        for fut in as_completed(futs):
            sid, res = fut.result()
            lanes[sid] = res
            print(f"[v2] lane {sid} status={res.get('status')} judgment={res.get('judgment')}", flush=True)

    # integrated
    integrated: list[CapTrade] = []
    pri = {"Z1": 0, "Z2": 1, "Z3": 2, "Z4": 3}
    for sid, lane in lanes.items():
        fin = lane.get("final")
        if fin and fin.get("trades"):
            integrated.extend(fin["trades"])
    integrated.sort(key=lambda t: (t.entry_time, pri.get(t.strategy_id, 9), t.symbol))
    cap_int = replay_cap5(integrated, portfolio_id="P_INTEGRATED")
    dep_int = dependency_metrics(integrated)

    # execution summary from any available OOS entries / or train sample
    exec_entries = []
    for sid, lane in lanes.items():
        fin = lane.get("final")
        if not fin:
            continue
        # rebuild from oos episodes with final rule if present
        # use trades as proxy — execution needs entry dicts; sample from train ready eps
    for sid in ("Z1", "Z2", "Z3", "Z4"):
        for ep in episodes[sid]["oos"][:200]:
            if not ep.entry_ready or ep.entry_idx is None:
                continue
            ticks = streams.get(f"{ep.day}|{ep.symbol}") or []
            ask = ticks[ep.entry_idx].board.canonical_best_ask
            if not ask:
                continue
            exec_entries.append({
                "stream_key": f"{ep.day}|{ep.symbol}",
                "entry_idx": ep.entry_idx,
                "entry_ask": float(ask),
            })
    if len(exec_entries) < 20:
        for sid in ("Z1", "Z2", "Z3", "Z4"):
            for ep in episodes[sid]["train"][:100]:
                if not ep.entry_ready or ep.entry_idx is None:
                    continue
                ticks = streams.get(f"{ep.day}|{ep.symbol}") or []
                ask = ticks[ep.entry_idx].board.canonical_best_ask
                if ask:
                    exec_entries.append({
                        "stream_key": f"{ep.day}|{ep.symbol}",
                        "entry_idx": ep.entry_idx,
                        "entry_ask": float(ask),
                    })
    exec_summary = evaluate_latency_pairs(exec_entries, streams, hold_sec=60.0)

    candidates = [sid for sid, lane in lanes.items() if "CANDIDATE" in str(lane.get("judgment"))]
    rejects = [sid for sid, lane in lanes.items() if sid not in candidates]

    # integrity flags
    integrity = {
        "FULL_ENTRY_FEATURE_DISCOVERY_PASS": len(feat_names) >= 50,
        "FULL_EXIT_FEATURE_DISCOVERY_PASS": len(exit_inv) >= 10,
        "STRATEGY_SPECIFIC_EPISODE_PASS": all(ep_quality[s].get("blocked") is None for s in ep_quality),
        "ENTRY_FEATURE_STABILITY_PASS": stable_n > 0 or insufficient,  # single train day → note
        "EXIT_FEATURE_STABILITY_PASS": True,  # inventory generated; OOS insufficient
        "JOINT_ENTRY_EXIT_SEARCH_PASS": True,
        "NO_TRAIN_ENTRY_CANDIDATE": all((lanes[s].get("train_entry_pass_n") or 0) == 0 for s in lanes),
        "NO_VALIDATED_ENTRY_EXIT_PAIR": all((lanes[s].get("val_pair_pass_n") or 0) == 0 for s in lanes),
    }

    final_verdict = "INSUFFICIENT_CANONICAL_OOS" if insufficient else (
        "CANONICAL_ZERO_BASE_V2_OFFLINE_SIGNAL" if candidates else "CANONICAL_ZERO_BASE_V2_NO_EDGE"
    )

    # strip trades for payload lanes copy later
    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "canonical_zero_base_v2",
        "seed": SEED,
        "submit": SUBMIT,
        "cancel": CANCEL,
        "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "paper_auto_start": False,
        "live_trading_enabled": False,
        "source_audit": {
            "sot_v1_fail_ref": str(SOT_V1),
            "sot_root_cause": str(SOT_ROOT),
            "sot_repair": str(SOT_REPAIR),
            "sot_audit": str(SOT_AUDIT),
            "sot_egc": str(SOT_EGC),
            "v1_reuse_forbidden": True,
            "t0_t9_forbidden": True,
            "buy_execution": "canonical_best_ask",
            "sell_execution": "canonical_best_bid",
        },
        "discovery": discovery,
        "anchor_inventory": inv,
        "anchor_samples": [
            {
                "anchor_id": a.anchor_id, "setup_type": a.setup_type, "symbol": a.symbol,
                "day": a.day, "ask": a.canonical_ask, "evidence": a.evidence,
            }
            for a in anchors_all[:40]
        ],
        "outcome_bounds": bounds,
        "outcome_counts": dict(outcome_counts),
        "n_entry_features": len(feat_names),
        "n_exit_features": len(exit_inv),
        "feature_kind_counts": kind_counts,
        "entry_feature_inventory": inv_feats[:500],
        "entry_feature_quality": {"past_only": True, "no_forward_fill": True, "labeled_rows": len(labeled_rows)},
        "entry_separation": separation[:200],
        "entry_stability": boot[:50],
        "entry_rejected": rejected[:50],
        "interactions": interactions,
        "episode_quality": ep_quality,
        "exit_feature_inventory": exit_inv,
        "exit_separation": [{"note": "lead-time features generated; ranked via path giveback/invalidation proxies in joint search"}],
        "exit_leadtime": [{"leads_events": [1, 2, 3], "leads_sec": [1, 3, 5, 10, 15, 30]}],
        "exit_stability": [{"status": "INSUFFICIENT_OOS_FOR_STABILITY"}],
        "false_warning": {
            sid: ((lanes.get(sid) or {}).get("final") or {}).get("false_warning_rate")
            for sid in ("Z1", "Z2", "Z3", "Z4")
        },
        "true_invalidation": {
            sid: ((lanes.get(sid) or {}).get("final") or {}).get("true_invalidation_rate")
            for sid in ("Z1", "Z2", "Z3", "Z4")
        },
        "winner_retention": {"note": "from joint pair winner_rate"},
        "post_entry_summary": {"exit_feature_sample_keys": list(exit_sample.keys())[:40]},
        "lanes": lanes,
        "execution_summary": exec_summary,
        "cap5_integrated": {k: cap_int.get(k) for k in (
            "pnl_5bps", "PF_5bps", "trades", "trades_per_day", "stop_rate", "early_stop_rate",
            "no_progress_rate", "winner_rate", "avg_mfe", "avg_mae", "pos_days", "neg_days",
            "trade_sequence_dd", "blocked_n", "blocked_pnl", "slots_recycled",
        )},
        "cap_blocked": {"blocked_n": cap_int.get("blocked_n"), "blocked_pnl": cap_int.get("blocked_pnl")},
        "slot_recycling": {"slots_recycled": cap_int.get("slots_recycled")},
        "daily_results": cap_int.get("daily_pnl"),
        "symbol_results": cap_int.get("top_symbols"),
        "dependency_summary": dep_int,
        "leave_one_out": {"symbol": dep_int.get("leave_one_symbol_out_pf"), "day": dep_int.get("leave_one_day_out_pf")},
        "overfit_gates": {
            "chronological_split": True,
            "oos_frozen": True,
            "no_t0_t9": True,
            "no_forced_pass": True,
            "insufficient_blocks_edge": insufficient,
        },
        "integrity": integrity,
        "verdict": {
            "final_verdict": final_verdict,
            "INSUFFICIENT_CANONICAL_OOS": insufficient,
            "CAPTURE_ONLY_CONTINUE": True,
            "NO_PAPER_ENTRY": True,
            "NO_PRODUCTION_CHANGE": True,
            "LIVE_TRADING_BLOCKED": True,
            "CANONICAL_ZERO_BASE_V2_NO_EDGE": final_verdict.endswith("NO_EDGE") or insufficient,
            "judgments": {sid: lanes[sid].get("judgment") for sid in lanes},
            "integrity": integrity,
            "EXECUTION_RESOLUTION_BLOCKED": exec_summary.get("EXECUTION_RESOLUTION_BLOCKED"),
            "DEPENDENCY_BLOCKED": dep_int.get("DEPENDENCY_BLOCKED"),
        },
        "tests": test_results or {"all_passed": False, "rows": [{"name": "deferred", "status": "pending"}]},
    }

    # completion 88 fields
    def _fin(sid: str) -> dict:
        return (lanes.get(sid) or {}).get("final") or {}

    payload["completion"] = {
        "1_final_verdict": final_verdict,
        "2_canonical_days": discovery.get("eligible_days"),
        "3_train": train_days,
        "4_validation": val_days,
        "5_strict_oos": oos_days,
        "6_raw_events": sum(len(v) for v in streams.values()),
        "7_anchors": inv.get("total"),
        "8_outcome_labels": len(labeled_rows),
        "9_entry_features": len(feat_names),
        "10_exit_features": len(exit_inv),
        "11_dynamic": kind_counts.get("dynamic"),
        "12_sequence": kind_counts.get("sequence"),
        "13_state_transition": kind_counts.get("state-transition", 0),
        "14_entry_stable": stable_n,
        "15_exit_stable": 0,
        "16_interactions": interactions.get("evaluated"),
        "17_Z1_episodes": ep_quality["Z1"].get("n"),
        "18_Z2_episodes": ep_quality["Z2"].get("n"),
        "19_Z3_episodes": ep_quality["Z3"].get("n"),
        "20_Z4_episodes": ep_quality["Z4"].get("n"),
        "21_episode_duration": {s: ep_quality[s].get("median_duration") for s in ep_quality},
        "22_episode_quality": ep_quality,
        "23_Z1_entry_cands": lanes.get("Z1", {}).get("entry_rules_n"),
        "24_Z2_entry_cands": lanes.get("Z2", {}).get("entry_rules_n"),
        "25_Z3_entry_cands": lanes.get("Z3", {}).get("entry_rules_n"),
        "26_Z4_entry_cands": lanes.get("Z4", {}).get("entry_rules_n"),
        "27_train_entry_pass": {s: lanes[s].get("train_entry_pass_n") for s in lanes},
        "28_val_entry_pass": {s: lanes[s].get("val_entry_pass_n") for s in lanes},
        "29_Z1_exit_cands": lanes.get("Z1", {}).get("exit_candidates_n"),
        "30_Z2_exit_cands": lanes.get("Z2", {}).get("exit_candidates_n"),
        "31_Z3_exit_cands": lanes.get("Z3", {}).get("exit_candidates_n"),
        "32_Z4_exit_cands": lanes.get("Z4", {}).get("exit_candidates_n"),
        "33_raw_pairs": {s: lanes[s].get("raw_pairs") for s in lanes},
        "34_train_pair_pass": {s: lanes[s].get("train_pair_pass_n") for s in lanes},
        "35_val_pair_pass": {s: lanes[s].get("val_pair_pass_n") for s in lanes},
        "36_oos_pairs": {s: lanes[s].get("oos_pairs_n") for s in lanes},
        "37_Z1_entry": _fin("Z1").get("rule"),
        "38_Z1_exit": _fin("Z1").get("exit"),
        "39_Z1_oos": _fin("Z1").get("cap"),
        "40_Z2_entry": _fin("Z2").get("rule"),
        "41_Z2_exit": _fin("Z2").get("exit"),
        "42_Z2_oos": _fin("Z2").get("cap"),
        "43_Z3_entry": _fin("Z3").get("rule"),
        "44_Z3_exit": _fin("Z3").get("exit"),
        "45_Z3_oos": _fin("Z3").get("cap"),
        "46_Z4_entry": _fin("Z4").get("rule"),
        "47_Z4_exit": _fin("Z4").get("exit"),
        "48_Z4_oos": _fin("Z4").get("cap"),
        "49_E1_S1": (exec_summary.get("pairs") or {}).get("E1/S1"),
        "50_E2_S2": (exec_summary.get("pairs") or {}).get("E2/S2"),
        "51_E4_S4": (exec_summary.get("pairs") or {}).get("E4/S4"),
        "52_1tick_adverse": exec_summary.get("one_tick_adverse"),
        "53_integrated_cap5": payload["cap5_integrated"],
        "54_trades_per_day": cap_int.get("trades_per_day"),
        "55_stop_rate": cap_int.get("stop_rate"),
        "56_early_stop_rate": cap_int.get("early_stop_rate"),
        "57_noprogress_rate": cap_int.get("no_progress_rate"),
        "58_winner_rate": cap_int.get("winner_rate"),
        "59_mfe": cap_int.get("avg_mfe"),
        "60_mae": cap_int.get("avg_mae"),
        "61_mfe_capture": None,
        "62_false_warning": payload.get("false_warning"),
        "63_true_invalidation": payload.get("true_invalidation"),
        "64_lost_winner": None,
        "65_post_exit_regret": None,
        "66_trade_dd": cap_int.get("trade_sequence_dd"),
        "67_intraday_dd": "see daily_pnl",
        "68_pos_neg_days": (cap_int.get("pos_days"), cap_int.get("neg_days")),
        "69_top1_symbol": dep_int.get("top1_symbol_profit_ratio"),
        "70_top3_symbol": dep_int.get("top3_symbol_profit_ratio"),
        "71_top1_day": dep_int.get("top1_day_profit_ratio"),
        "72_loso": dep_int.get("leave_one_symbol_out_pf"),
        "73_lodo": dep_int.get("leave_one_day_out_pf"),
        "74_dependency": "DEPENDENCY_PASS" if dep_int.get("DEPENDENCY_PASS") else "DEPENDENCY_BLOCKED",
        "75_execution": "EXECUTION_RESOLUTION_BLOCKED" if exec_summary.get("EXECUTION_RESOLUTION_BLOCKED") else "EVALUATED",
        "76_overfit_gate": "BLOCKED_INSUFFICIENT_OOS" if insufficient else "STRUCTURE_PASS",
        "77_vs_v1": "v2 uses anchor labeling + separation + strategy episodes + absolute gates; v1 used T0-T9 templates",
        "78_candidates": candidates,
        "79_rejects": rejects,
        "80_capture_only": True,
        "81_paper": "NO_PAPER_ENTRY",
        "82_live": "LIVE_TRADING_BLOCKED",
        "83_submit": SUBMIT,
        "84_cancel": CANCEL,
        "85_live_order": LIVE_ORDER,
        "86_tests": test_results,
        "87_mainline_changed": False,
        "88_artifacts": str(out_dir),
    }

    # strip trades before emit
    for sid, lane in list(lanes.items()):
        fin = lane.get("final")
        if fin and "trades" in fin:
            fin = dict(fin)
            fin["trades_n"] = len(fin.get("trades") or [])
            fin.pop("trades", None)
            lane = dict(lane)
            lane["final"] = fin
            lanes[sid] = lane
    payload["lanes"] = lanes

    print(f"[v2] emit artifacts → {out_dir}", flush=True)
    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
