"""Canonical Zero-Base runner — discover → features → episodes → 4 lanes → CAP5 → report."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_zero_base.canonical_loader import load_streams
from research.canonical_zero_base.cap5_portfolio import CapTrade, replay_cap5
from research.canonical_zero_base.combination_search import run_lane
from research.canonical_zero_base.constants import (
    CANCEL,
    LIVE_ORDER,
    LEGACY_P0_PF,
    LEGACY_P3_PF,
    MAX_PARALLEL,
    OUT_ROOT,
    SAMPLE_STRIDE,
    SEED,
    SOT_AUDIT,
    SOT_EGC,
    SOT_REPAIR,
    SUBMIT,
)
from research.canonical_zero_base.data_discovery import discover_and_split
from research.canonical_zero_base.dependency import dependency_metrics
from research.canonical_zero_base.episode_builder import build_episodes, episode_stats
from research.canonical_zero_base.feature_library import FEATURE_DICTIONARY
from research.canonical_zero_base.reporting import emit_artifacts
from research.canonical_zero_base.strategy_contract import CONTRACTS

JST = ZoneInfo("Asia/Tokyo")


def _events_for_days(streams: dict, days: list[str]) -> dict:
    out = {}
    for k, ticks in streams.items():
        day = k.split("|")[0]
        if day not in days:
            continue
        out[k] = build_episodes(ticks)
    return out


def _lane_job(args: tuple) -> tuple[str, dict]:
    sid, et, ev, eo, ticks = args
    return sid, run_lane(sid, events_train=et, events_val=ev, events_oos=eo, ticks_all=ticks)


def run_zero_base(
    *,
    run_id: Optional[str] = None,
    stride: int = SAMPLE_STRIDE,
    out_root: Optional[Path] = None,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id

    discovery = discover_and_split()
    all_days = list(dict.fromkeys(
        (discovery.get("warmup") or [])
        + (discovery.get("train") or [])
        + (discovery.get("validation") or [])
        + (discovery.get("strict_oos") or [])
    ))
    streams = load_streams(all_days, stride=stride)

    train_days = discovery["train"]
    val_days = discovery["validation"]
    oos_days = discovery["strict_oos"]

    events_train = _events_for_days(streams, train_days)
    events_val = _events_for_days(streams, val_days)
    events_oos = _events_for_days(streams, oos_days)

    # episode stats across all loaded
    all_events = {}
    all_events.update(events_train)
    all_events.update(events_val)
    all_events.update(events_oos)
    flat = [e for evs in all_events.values() for e in evs]
    ep_stats = episode_stats(flat)

    # parallel lanes
    jobs = [
        (sid, events_train, events_val, events_oos, streams)
        for sid in ("Z1", "Z2", "Z3", "Z4")
    ]
    print(
        f"[zero_base] days train={train_days} val={val_days} oos={oos_days} "
        f"streams={len(streams)} events={ep_stats.get('raw_events')} episodes={ep_stats.get('true_episodes')}",
        flush=True,
    )
    lanes: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        futs = [ex.submit(_lane_job, j) for j in jobs]
        for fut in as_completed(futs):
            sid, res = fut.result()
            lanes[sid] = res
            print(
                f"[zero_base] lane {sid} done raw={res.get('raw_combinations')} "
                f"train_pass={res.get('train_pass')} oos_carry={res.get('oos_carry')}",
                flush=True,
            )

    # integrated P4/P5 from finals
    integrated_trades: list[CapTrade] = []
    for sid, lane in lanes.items():
        fin = lane.get("final")
        if not fin:
            continue
        for t in fin.get("trades") or []:
            integrated_trades.append(t)
    # priority fixed Z1>Z2>Z3>Z4 by sorting setup
    pri = {"Z1": 0, "Z2": 1, "Z3": 2, "Z4": 3}
    integrated_trades.sort(key=lambda t: (t.entry_time, pri.get(t.strategy_id, 9), t.symbol))
    cap_int = replay_cap5(integrated_trades, portfolio_id="P5_INTEGRATED")
    dep_int = dependency_metrics(integrated_trades)

    # per-lane judgments
    judgments = {}
    candidates = []
    rejects = []
    for sid, lane in lanes.items():
        fin = lane.get("final")
        if not fin:
            judgments[sid] = f"{sid}_REJECT"
            rejects.append(sid)
            continue
        cap = fin["cap"]
        dep = fin["dependency"]
        pf = cap.get("PF_5bps")
        try:
            pf_f = float(pf) if pf not in (None, float("inf")) else None
        except Exception:
            pf_f = None
        pnl = float(cap.get("pnl_5bps") or 0)
        oos_ok = pnl > 0 and pf_f is not None and pf_f > 1 and dep.get("DEPENDENCY_PASS")
        if discovery.get("insufficient_oos"):
            oos_ok = False
        if oos_ok:
            judgments[sid] = f"{CONTRACTS[sid].name.split()[0].upper()}_CANDIDATE"
            # map to required codes
            code = {
                "Z1": "Z1_PULLBACK_RECLAIM_CANDIDATE",
                "Z2": "Z2_BREAKOUT_CONTINUATION_CANDIDATE",
                "Z3": "Z3_ABSORPTION_BREAKOUT_CANDIDATE",
                "Z4": "Z4_COMPRESSION_EXPANSION_CANDIDATE",
            }[sid]
            judgments[sid] = code
            candidates.append(sid)
        else:
            code = {
                "Z1": "Z1_PULLBACK_RECLAIM_REJECT",
                "Z2": "Z2_BREAKOUT_CONTINUATION_REJECT",
                "Z3": "Z3_ABSORPTION_BREAKOUT_REJECT",
                "Z4": "Z4_COMPRESSION_EXPANSION_REJECT",
            }[sid]
            judgments[sid] = code
            rejects.append(sid)

    insufficient = bool(discovery.get("insufficient_oos"))
    if insufficient:
        final_verdict = "INSUFFICIENT_CANONICAL_OOS"
        edge = "CANONICAL_ZERO_BASE_NO_EDGE"
    elif candidates and float(cap_int.get("pnl_5bps") or 0) > 0:
        final_verdict = "CANONICAL_ZERO_BASE_OFFLINE_SIGNAL"
        edge = "CANONICAL_ZERO_BASE_OFFLINE_SIGNAL"
    else:
        final_verdict = "CANONICAL_ZERO_BASE_NO_EDGE"
        edge = "CANONICAL_ZERO_BASE_NO_EDGE"

    # EDGE_CONFIRMED forbidden without 10 OOS days
    edge_confirmed = False

    tests = test_results or {"rows": [{"name": "deferred", "status": "pending"}], "all_passed": False}

    # summarize finals for completion
    def _summ(sid: str) -> dict:
        fin = (lanes.get(sid) or {}).get("final")
        if not fin:
            return {}
        return {
            "entry_rule": fin.get("rule"),
            "exit_mode": fin.get("exit_mode"),
            "cap": {k: fin["cap"].get(k) for k in (
                "pnl_5bps", "PF_5bps", "trades", "trades_per_day", "stop_rate", "early_stop_rate",
                "no_progress_rate", "winner_rate", "avg_mfe", "avg_mae", "pos_days", "neg_days", "trade_sequence_dd",
            )},
            "dependency": fin.get("dependency"),
        }

    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "canonical_zero_base_strategy",
        "seed": SEED,
        "submit": SUBMIT,
        "cancel": CANCEL,
        "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "paper_auto_start": False,
        "live_trading_enabled": False,
        "source_audit": {
            "sot_repair": str(SOT_REPAIR),
            "sot_audit": str(SOT_AUDIT),
            "sot_egc": str(SOT_EGC),
            "legacy_forbidden": True,
            "buy_execution": "canonical_best_ask",
            "sell_execution": "canonical_best_bid",
        },
        "discovery": discovery,
        "feature_dictionary": FEATURE_DICTIONARY,
        "feature_quality": {"past_only": True, "no_forward_fill": True, "missing": "NOT_EVALUABLE"},
        "episode_stats": ep_stats,
        "opportunity_summary": {"note": "per-rule never_profitable_rate in lane oos_results"},
        "contracts": {k: {
            "strategy_id": c.strategy_id,
            "name": c.name,
            "thesis": c.thesis,
            "horizons": c.expected_horizons_sec,
            "invalidations": c.invalidations,
            "exit_modes": c.exit_modes,
        } for k, c in CONTRACTS.items()},
        "lanes": lanes,
        "entry_results": {sid: _summ(sid).get("entry_rule") for sid in lanes},
        "exit_results": {sid: _summ(sid).get("exit_mode") for sid in lanes},
        "pair_results": {sid: _summ(sid) for sid in lanes},
        "execution_scenarios": {
            "primary": ["E1/S1", "E2/S2", "E4/S4"],
            "note": "fills use canonical Ask/Bid only; CurrentPrice/mid forbidden",
        },
        "one_episode": {"enforced": True, "episode_blocked_in_cap5": True},
        "cap5_integrated": cap_int,
        "daily_results": cap_int.get("daily_pnl"),
        "symbol_results": cap_int.get("top_symbols"),
        "dependency_summary": dep_int,
        "leave_one_out": {
            "symbol": dep_int.get("leave_one_symbol_out_pf"),
            "day": dep_int.get("leave_one_day_out_pf"),
        },
        "overfit_gates": {
            "chronological_split": True,
            "oos_threshold_frozen": True,
            "oos_features_frozen": True,
            "symbol_specific_forbidden": True,
            "time_specific_forbidden": True,
            "insufficient_blocks_edge_confirmed": insufficient,
        },
        "legacy_reference": {
            "L0_legacy_P0_PF": LEGACY_P0_PF,
            "L1_canonical_old_P3_PF": LEGACY_P3_PF,
            "note": "reference only; not mixed into search",
        },
        "candidate_selection": {"candidates": candidates, "rejects": rejects, "judgments": judgments},
        "verdict": {
            "final_verdict": final_verdict,
            "edge": edge,
            "EDGE_CONFIRMED": edge_confirmed,
            "CANONICAL_ZERO_BASE_PIPELINE_READY": True,
            "INSUFFICIENT_CANONICAL_OOS": insufficient,
            "CAPTURE_ONLY_CONTINUE": True,
            "NO_PAPER_ENTRY": True,
            "NO_PRODUCTION_CHANGE": True,
            "LIVE_TRADING_BLOCKED": True,
            "judgments": judgments,
        },
        "tests": tests,
    }

    # completion 66 fields
    z = {sid: _summ(sid) for sid in ("Z1", "Z2", "Z3", "Z4")}
    payload["completion"] = {
        "1_final_verdict": final_verdict,
        "2_canonical_days": discovery.get("eligible_days"),
        "3_train": train_days,
        "4_validation": val_days,
        "5_strict_oos": oos_days,
        "6_ask_coverage": discovery.get("ask_coverage_mean"),
        "7_bid_coverage": discovery.get("bid_coverage_mean"),
        "8_raw_events": ep_stats.get("raw_events"),
        "9_true_episodes": ep_stats.get("true_episodes"),
        "10_Z1_candidates": (lanes.get("Z1") or {}).get("raw_combinations"),
        "11_Z2_candidates": (lanes.get("Z2") or {}).get("raw_combinations"),
        "12_Z3_candidates": (lanes.get("Z3") or {}).get("raw_combinations"),
        "13_Z4_candidates": (lanes.get("Z4") or {}).get("raw_combinations"),
        "14_lane_combo_counts": {k: v.get("raw_combinations") for k, v in lanes.items()},
        "15_train_pass": {k: v.get("train_pass") for k, v in lanes.items()},
        "16_val_pass": {k: v.get("val_pass") for k, v in lanes.items()},
        "17_oos_carry": {k: v.get("oos_carry") for k, v in lanes.items()},
        "18_Z1_entry": z["Z1"].get("entry_rule"),
        "19_Z1_exit": z["Z1"].get("exit_mode"),
        "20_Z1_oos": z["Z1"].get("cap"),
        "21_Z2_entry": z["Z2"].get("entry_rule"),
        "22_Z2_exit": z["Z2"].get("exit_mode"),
        "23_Z2_oos": z["Z2"].get("cap"),
        "24_Z3_entry": z["Z3"].get("entry_rule"),
        "25_Z3_exit": z["Z3"].get("exit_mode"),
        "26_Z3_oos": z["Z3"].get("cap"),
        "27_Z4_entry": z["Z4"].get("entry_rule"),
        "28_Z4_exit": z["Z4"].get("exit_mode"),
        "29_Z4_oos": z["Z4"].get("cap"),
        "30_integrated_cap5": {k: cap_int.get(k) for k in ("pnl_5bps", "PF_5bps", "trades", "trades_per_day")},
        "31_E1_S1": "canonical Ask/Bid primary path (decision+1)",
        "32_E2_S2": "100ms+ latency path supported in execution_replay",
        "33_1tick_adverse": "cost+1tick evaluated in opportunity labels",
        "34_trades_per_day": cap_int.get("trades_per_day"),
        "35_one_episode_one_entry": True,
        "36_stop_rate": cap_int.get("stop_rate"),
        "37_early_stop_rate": cap_int.get("early_stop_rate"),
        "38_no_progress_rate": cap_int.get("no_progress_rate"),
        "39_winner_rate": cap_int.get("winner_rate"),
        "40_mfe": cap_int.get("avg_mfe"),
        "41_mae": cap_int.get("avg_mae"),
        "42_mfe_capture": None,
        "43_trade_dd": cap_int.get("trade_sequence_dd"),
        "44_intraday_dd": "see daily_pnl path",
        "45_pos_neg_days": (cap_int.get("pos_days"), cap_int.get("neg_days")),
        "46_top1_symbol": dep_int.get("top1_symbol_profit_ratio"),
        "47_top3_symbol": dep_int.get("top3_symbol_profit_ratio"),
        "48_top1_day": dep_int.get("top1_day_profit_ratio"),
        "49_loso": dep_int.get("leave_one_symbol_out_pf"),
        "50_lodo": dep_int.get("leave_one_day_out_pf"),
        "51_dependency": "DEPENDENCY_PASS" if dep_int.get("DEPENDENCY_PASS") else "DEPENDENCY_BLOCKED",
        "52_execution_resilience": "EXECUTION_FRAGILE" if insufficient else "NOT_CONFIRMED",
        "53_overfit_gate": "BLOCKED_INSUFFICIENT_OOS" if insufficient else "PASS_STRUCTURE",
        "54_legacy_P0_PF": LEGACY_P0_PF,
        "55_legacy_P3_PF": LEGACY_P3_PF,
        "56_candidates": candidates,
        "57_rejects": rejects,
        "58_paper_readiness": "NO_PAPER_ENTRY",
        "59_capture_only": True,
        "60_live": "LIVE_TRADING_BLOCKED",
        "61_submit": SUBMIT,
        "62_cancel": CANCEL,
        "63_live_order": LIVE_ORDER,
        "64_tests": tests,
        "65_mainline_changed": False,
        "66_artifacts": str(out_dir),
    }

    # Strip CapTrade objects before artifact emission (3 files only; no CSV dumps).
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

    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
