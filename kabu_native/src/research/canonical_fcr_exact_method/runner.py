"""Canonical FCR exact-method runner."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_fcr_exact_method.arms import collect_arms, fit_thresholds_train, train_gate, val_gate
from research.canonical_fcr_exact_method.constants import (
    CANCEL, LIVE_ORDER, OUT_ROOT, SEED, SOT_AUDIT, SOT_EGC, SOT_REPAIR, SOT_VCIE, SUBMIT,
)
from research.canonical_fcr_exact_method.data_split import discover_and_split
from research.canonical_fcr_exact_method.execution import evaluate_execution
from research.canonical_fcr_exact_method.loader import load_streams
from research.canonical_fcr_exact_method.observations import causal_vwap
from research.canonical_fcr_exact_method.opportunity import evaluate_candidates, increment_effect
from research.canonical_fcr_exact_method.reporting import emit

JST = ZoneInfo("Asia/Tokyo")
ARMS = (
    "F0_RECLAIM_ONLY", "F1_TREND_RECLAIM", "F2_PULLBACK_RECLAIM", "F3_SELLING_EXHAUSTED",
    "F4_BUY_FLOW_CONFIRMED", "F5_FULL_FCR", "D1_NO_EXHAUSTION", "D2_NO_BUY_FLOW",
)


def _summ(ev: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "n", "pnl", "pf", "mean", "never_rate", "early_adverse_rate", "stop_rate",
        "stop_5m_rate", "noprogress_rate", "winner_rate", "avg_mfe", "avg_mae", "top1_symbol_share",
    )
    return {k: ev.get(k) for k in keys}


def run_fcr(
    *,
    run_id: Optional[str] = None,
    stride: int = 6,
    out_root: Optional[Path] = None,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id
    print("[fcr] split…", flush=True)
    discovery = discover_and_split()
    days = list(dict.fromkeys(
        (discovery.get("warmup") or [])
        + (discovery.get("train") or [])
        + (discovery.get("validation") or [])
        + (discovery.get("forensic_holdout") or [])
    ))
    print(f"[fcr] load {days} stride={stride}", flush=True)
    streams = load_streams(days, stride=stride)
    n_ticks = sum(len(v) for v in streams.values())
    print(f"[fcr] streams={len(streams)} ticks={n_ticks}", flush=True)

    # VWAP audit sample
    vwap_ok = vwap_ne = 0
    for ticks in list(streams.values())[:30]:
        if len(ticks) > 50:
            r = causal_vwap(ticks, 50)
            if r["status"] == "OK":
                vwap_ok += 1
            else:
                vwap_ne += 1
    vwap_audit = {
        "status": "OK" if vwap_ok > 0 else "VWAP_NOT_EVALUABLE",
        "sample_ok": vwap_ok,
        "sample_not_evaluable": vwap_ne,
        "note": "causal px*volume_delta only; no fabrication",
    }

    train_days = discovery["train"]
    val_days = discovery["validation"]
    hold_days = discovery["forensic_holdout"]

    print("[fcr] fit thresholds TRAIN…", flush=True)
    params = fit_thresholds_train(streams, train_days)
    thr = {k: v for k, v in params.items() if k != "diagnostics"}
    print(f"[fcr] thresholds={thr}", flush=True)

    def eval_days(dlist: list[str]):
        pack = collect_arms(streams, dlist, params=thr)
        eps = pack.pop("_episodes")
        f0e = pack.pop("_f0")
        res = {a: evaluate_candidates(pack[a], streams) for a in ARMS}
        return res, pack, eps, f0e

    print("[fcr] TRAIN…", flush=True)
    train_res, train_arms, train_eps, _ = eval_days(train_days)
    print("[fcr] VALIDATION…", flush=True)
    val_res, val_arms, _, _ = eval_days(val_days)
    print("[fcr] forensic holdout…", flush=True)
    hold_res, hold_arms, hold_eps, _ = eval_days(hold_days)

    inc = {
        "F0_to_F1": increment_effect(train_res["F0_RECLAIM_ONLY"], train_res["F1_TREND_RECLAIM"]),
        "F1_to_F2": increment_effect(train_res["F1_TREND_RECLAIM"], train_res["F2_PULLBACK_RECLAIM"]),
        "F2_to_F3": increment_effect(train_res["F2_PULLBACK_RECLAIM"], train_res["F3_SELLING_EXHAUSTED"]),
        "F3_to_F4": increment_effect(train_res["F3_SELLING_EXHAUSTED"], train_res["F4_BUY_FLOW_CONFIRMED"]),
        "F4_to_F5": increment_effect(train_res["F4_BUY_FLOW_CONFIRMED"], train_res["F5_FULL_FCR"]),
        "D1_vs_F3": increment_effect(train_res["D1_NO_EXHAUSTION"], train_res["F3_SELLING_EXHAUSTED"]),
        "D2_vs_F5": increment_effect(train_res["D2_NO_BUY_FLOW"], train_res["F5_FULL_FCR"]),
    }

    tg_ok, tg_reason = train_gate(
        train_res["F5_FULL_FCR"], train_res["F0_RECLAIM_ONLY"],
        train_res["F2_PULLBACK_RECLAIM"], train_res["F3_SELLING_EXHAUSTED"],
    )
    vg_ok, vg_reason = (False, "SKIPPED_NO_TRAIN")
    if tg_ok:
        vg_ok, vg_reason = val_gate(val_res["F5_FULL_FCR"])

    # counts from train episodes
    counts = {
        "trend_context": sum(1 for e in train_eps if e.flags.get("has_trend")),
        "initial_impulses": len({e.impulse_id for e in train_eps}),
        "pullbacks": sum(1 for e in train_eps if e.flags.get("has_pullback")),
        "valid_pullbacks": sum(1 for e in train_eps if e.flags.get("has_pullback")),
        "selling_exhaustion": sum(1 for e in train_eps if e.flags.get("has_exhaustion")),
        "buy_flow": sum(1 for e in train_eps if e.flags.get("has_buy_flow")),
        "reclaim_levels": sum(1 for e in train_eps if e.reclaim_level is not None),
        "reclaim_triggers": sum(1 for e in train_eps if e.flags.get("has_reclaim")),
        "entry_ready": sum(1 for e in train_eps if e.status == "ENTRY_READY"),
    }
    ep_stats = {
        "n": len(train_eps),
        "expired": sum(1 for e in train_eps if e.status == "EXPIRED"),
        "invalidated": sum(1 for e in train_eps if e.status == "INVALIDATED"),
        "entry_ready": counts["entry_ready"],
    }
    # one impulse one entry
    imp_entries = {}
    for c in train_arms["F5_FULL_FCR"]:
        imp_entries[c.impulse_id] = imp_entries.get(c.impulse_id, 0) + 1
    one_impulse = {
        "pass": all(v <= 1 for v in imp_entries.values()) if imp_entries else True,
        "max_per_impulse": max(imp_entries.values()) if imp_entries else 0,
        "verdict": "ONE_IMPULSE_ONE_ENTRY_PASS" if (not imp_entries or max(imp_entries.values()) <= 1) else "ONE_IMPULSE_ONE_ENTRY_BLOCKED",
    }
    # symbol reentry diagnostic
    sym_n: dict[str, int] = {}
    for e in train_eps:
        if e.entry_idx is not None:
            sym_n[e.symbol] = sym_n.get(e.symbol, 0) + 1
    symbol_reentry = {"symbols_with_multi": sum(1 for v in sym_n.values() if v > 1), "detail_top": sorted(sym_n.items(), key=lambda x: -x[1])[:10]}

    exec_cands = train_arms["F5_FULL_FCR"] + val_arms["F5_FULL_FCR"]
    if vg_ok:
        exec_cands = hold_arms["F5_FULL_FCR"] or exec_cands
    execution = evaluate_execution(exec_cands, streams)

    cap5 = {"trades": 0, "pnl_5bps": 0.0, "note": "no_validated_candidate"}
    if vg_ok and hold_arms["F5_FULL_FCR"]:
        from research.canonical_zero_base_v2.cap5 import CapTrade, replay_cap5
        from research.canonical_fcr_exact_method.opportunity import path_metrics
        trades = []
        for c in hold_arms["F5_FULL_FCR"]:
            ticks = streams[c.stream_key]
            m = path_metrics(ticks, c.entry_idx, c.entry_ask, max_sec=180)
            if not m.get("evaluable"):
                continue
            t0 = ticks[c.entry_idx].ts
            exit_bid, exit_t = c.entry_ask, t0
            for j in range(c.entry_idx + 1, len(ticks)):
                if (ticks[j].ts - t0).total_seconds() >= 180:
                    b = ticks[j].board.canonical_best_bid
                    if b:
                        exit_bid, exit_t = float(b), ticks[j].ts
                    break
            trades.append(CapTrade(
                day=c.day, symbol=c.symbol, episode_id=c.episode_id,
                entry_time=c.entry_time, exit_time=exit_t,
                entry_price=c.entry_ask, exit_price=exit_bid,
                pnl_5bps=float(m["terminal_pnl_yen"]), exit_reason="R0_fixed_horizon",
                strategy_id="FCR", setup_id=c.impulse_id,
                session="AM" if c.entry_time.hour < 12 else "PM",
                mfe=float(m["mfe"]), mae=float(m["mae"]), winner=bool(m["winner"]),
            ))
        cap5 = replay_cap5(trades, portfolio_id="FCR_CAP5")

    # codes
    def inc_code(prefix: str, key: str) -> str:
        lab = (inc.get(key) or {}).get("label", "INCREMENT_NEGATIVE")
        if lab == "INCREMENT_POSITIVE":
            return f"{prefix}_INCREMENT_POSITIVE"
        if lab == "INCREMENT_MIXED":
            return f"{prefix}_INCREMENT_MIXED"
        return f"{prefix}_INCREMENT_NEGATIVE"

    codes = [
        "CANONICAL_FCR_DATA_READY",
        "F0_RECLAIM_BASELINE_EVALUATED",
        inc_code("F1_TREND", "F0_to_F1"),
        inc_code("F2_PULLBACK", "F1_to_F2"),
        inc_code("F3_EXHAUSTION", "F2_to_F3"),
        inc_code("F4_BUY_FLOW", "F3_to_F4"),
        inc_code("F5_RECLAIM_TRIGGER", "F4_to_F5"),
        one_impulse["verdict"],
        execution.get("resilience") or "EXECUTION_FRAGILE",
    ]
    if execution.get("EXECUTION_RESOLUTION_BLOCKED"):
        codes.append("EXECUTION_RESOLUTION_BLOCKED")
    if not tg_ok:
        entry_verdict = "NO_TRAIN_CANONICAL_FCR_CANDIDATE"
        codes += [entry_verdict, "CANONICAL_FCR_ENTRY_NO_EDGE", "FCR_EXIT_RESEARCH_BLOCKED"]
    elif not vg_ok:
        entry_verdict = "NO_VALIDATED_CANONICAL_FCR_CANDIDATE"
        codes += [entry_verdict, "CANONICAL_FCR_ENTRY_NO_EDGE", "FCR_EXIT_RESEARCH_BLOCKED"]
    else:
        entry_verdict = "CANONICAL_FCR_ENTRY_CANDIDATE"
        codes += [entry_verdict, "FCR_EXIT_RESEARCH_READY"]
    codes += [
        "INSUFFICIENT_FRESH_CANONICAL_OOS", "REUSED_FORENSIC_HOLDOUT",
        "CAPTURE_ONLY_CONTINUE", "NO_PAPER_ENTRY", "NO_PRODUCTION_CHANGE", "LIVE_TRADING_BLOCKED",
    ]

    f5t = train_res["F5_FULL_FCR"]
    ask_cov = bid_cov = 0.0
    n = 0
    for ticks in streams.values():
        for t in ticks[::100]:
            n += 1
            if t.board.canonical_best_ask:
                ask_cov += 1
            if t.board.canonical_best_bid:
                bid_cov += 1

    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "canonical_fcr_exact_method",
        "seed": SEED,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False, "paper_auto_start": False, "live_trading_enabled": False,
        "source_audit": {
            "sot_repair": str(SOT_REPAIR), "sot_audit": str(SOT_AUDIT), "sot_egc": str(SOT_EGC),
            "sot_vcie_ref": str(SOT_VCIE), "method": "F0_to_F5_incremental_fcr",
            "not_vcie": True, "pbv2_unchanged": True,
        },
        "split": discovery,
        "coverage": {"ask": ask_cov / n if n else 0, "bid": bid_cov / n if n else 0, "ticks": n_ticks},
        "vwap_audit": vwap_audit,
        "thresholds": thr,
        "threshold_diagnostics": params.get("diagnostics"),
        "counts": counts,
        "episode_stats": ep_stats,
        "one_impulse": one_impulse,
        "symbol_reentry": symbol_reentry,
        "state_note": {"machine": "IDLE→TREND→PULLBACK→EXHAUST→BUY_FLOW→RECLAIM→ENTRY_READY"},
        "board_flow_note": {"ask_depletion": "EXECUTED_DEPLETION vs CANCELLATION_OR_UNKNOWN"},
        "train_results": {a: _summ(train_res[a]) for a in ARMS},
        "val_results": {a: _summ(val_res[a]) for a in ARMS},
        "holdout_results": {a: _summ(hold_res[a]) for a in ARMS},
        "arm_results": {
            a: {"train": _summ(train_res[a]), "val": _summ(val_res[a]), "holdout": _summ(hold_res[a])}
            for a in ARMS
        },
        "incremental": inc,
        "train_gate": {"ok": tg_ok, "reason": tg_reason},
        "val_gate": {"ok": vg_ok, "reason": vg_reason},
        "execution": execution,
        "cap5": {k: cap5.get(k) for k in ("trades", "pnl_5bps", "PF_5bps", "trades_per_day", "pos_days", "neg_days", "note")},
        "cap_blocked": {"note": "n/a" if vg_ok else "no_validated_candidate"},
        "daily_results": cap5.get("daily_pnl"),
        "symbol_results": cap5.get("top_symbols"),
        "dependency": {"top1_symbol_share": f5t.get("top1_symbol_share")},
        "opportunity_note": {"entry": "canonical Ask E1", "future": "canonical Bid", "cost_bps": 5},
        "pbv2_compare": {
            "note": "FCR independent Watch50 evaluation; PBv2 mainline unchanged; matched EXIT uses R0 fixed horizon only",
            "pbv2_modified": False,
        },
        "reference_exits": {"R0": "fixed_horizon", "R1": "hard_stop+horizon", "R2": "pullback_low_break", "R3": "reclaim_rebreak", "complex_exit": "BLOCKED"},
        "verdict": {
            "final_verdict": "INSUFFICIENT_FRESH_CANONICAL_OOS",
            "entry_verdict": entry_verdict,
            "codes": codes,
            "REUSED_FORENSIC_HOLDOUT": True,
            "CAPTURE_ONLY_CONTINUE": True,
            "NO_PAPER_ENTRY": True,
            "NO_PRODUCTION_CHANGE": True,
            "LIVE_TRADING_BLOCKED": True,
            "FCR_EXIT_RESEARCH_READY": vg_ok,
            "FCR_EXIT_RESEARCH_BLOCKED": not vg_ok,
        },
        "tests": test_results or {"all_passed": False, "rows": [{"name": "deferred", "status": "pending"}]},
    }

    payload["completion"] = {
        "1_final_verdict": "INSUFFICIENT_FRESH_CANONICAL_OOS",
        "2_canonical_days": discovery.get("eligible_days"),
        "3_warmup": discovery.get("warmup"),
        "4_train": train_days,
        "5_validation": val_days,
        "6_forensic_holdout": hold_days,
        "7_raw_events": n_ticks,
        "8_vwap": vwap_audit["status"],
        "9_trend_context": counts["trend_context"],
        "10_initial_impulses": counts["initial_impulses"],
        "11_pullbacks": counts["pullbacks"],
        "12_valid_pullbacks": counts["valid_pullbacks"],
        "13_selling_exhaustion": counts["selling_exhaustion"],
        "14_buy_flow": counts["buy_flow"],
        "15_reclaim_triggers": counts["reclaim_triggers"],
        "16_entry_ready": counts["entry_ready"],
        "17_expired": ep_stats["expired"],
        "18_invalidated": ep_stats["invalidated"],
        "19_one_impulse": one_impulse,
        "20_symbol_reentry": symbol_reentry,
        "21_F0_n": train_res["F0_RECLAIM_ONLY"].get("n"),
        "22_F1_n": train_res["F1_TREND_RECLAIM"].get("n"),
        "23_F2_n": train_res["F2_PULLBACK_RECLAIM"].get("n"),
        "24_F3_n": train_res["F3_SELLING_EXHAUSTED"].get("n"),
        "25_F4_n": train_res["F4_BUY_FLOW_CONFIRMED"].get("n"),
        "26_F5_n": train_res["F5_FULL_FCR"].get("n"),
        "27_D1_n": train_res["D1_NO_EXHAUSTION"].get("n"),
        "28_D2_n": train_res["D2_NO_BUY_FLOW"].get("n"),
        "29_F0_train": _summ(train_res["F0_RECLAIM_ONLY"]),
        "30_F1_train": _summ(train_res["F1_TREND_RECLAIM"]),
        "31_F2_train": _summ(train_res["F2_PULLBACK_RECLAIM"]),
        "32_F3_train": _summ(train_res["F3_SELLING_EXHAUSTED"]),
        "33_F4_train": _summ(train_res["F4_BUY_FLOW_CONFIRMED"]),
        "34_F5_train": _summ(train_res["F5_FULL_FCR"]),
        "35_F0_to_F1": inc["F0_to_F1"],
        "36_F1_to_F2": inc["F1_to_F2"],
        "37_F2_to_F3": inc["F2_to_F3"],
        "38_F3_to_F4": inc["F3_to_F4"],
        "39_F4_to_F5": inc["F4_to_F5"],
        "40_D1": inc["D1_vs_F3"],
        "41_D2": inc["D2_vs_F5"],
        "42_train_pass": tg_ok,
        "43_val_results": {a: _summ(val_res[a]) for a in ("F0_RECLAIM_ONLY", "F5_FULL_FCR")},
        "44_val_pass": vg_ok,
        "45_holdout": _summ(hold_res["F5_FULL_FCR"]),
        "46_final_entry": {"arm": "F5_FULL_FCR", "thresholds": thr} if tg_ok else None,
        "47_state_machine": "IDLE→TREND_CONTEXT→PULLBACK_DETECTED→SELLING_EXHAUSTED→BUY_FLOW_CONFIRMED→RECLAIM_TRIGGERED→ENTRY_READY",
        "48_trend": {"slope_min": thr.get("slope_min")},
        "49_pullback": {"lo": thr.get("pb_lo"), "hi": thr.get("pb_hi")},
        "50_exhaustion": {"new_low_stop_sec": thr.get("new_low_stop_sec")},
        "51_buy_flow": {"buy_ratio": thr.get("buy_ratio"), "freq_accel": thr.get("freq_accel")},
        "52_reclaim": {"hold_events": thr.get("reclaim_hold_events")},
        "53_expiry": {"exh_to_buy": thr.get("expiry_exh_to_buy"), "buy_to_reclaim": thr.get("expiry_buy_to_reclaim")},
        "54_opp_pnl": f5t.get("pnl"),
        "55_opp_pf": f5t.get("pf"),
        "56_mean": f5t.get("mean"),
        "57_never": f5t.get("never_rate"),
        "58_stop": f5t.get("stop_rate"),
        "59_stop_5m": f5t.get("stop_5m_rate"),
        "60_noprogress": f5t.get("noprogress_rate"),
        "61_winner": f5t.get("winner_rate"),
        "62_mfe": f5t.get("avg_mfe"),
        "63_mae": f5t.get("avg_mae"),
        "64_cost_recovery": "in opportunity metrics",
        "65_E1": (execution.get("E0_E5") or {}).get("E1"),
        "66_E2": (execution.get("E0_E5") or {}).get("E2"),
        "67_E4": (execution.get("E0_E5") or {}).get("E4"),
        "68_1tick": execution.get("one_tick_adverse"),
        "69_execution": execution.get("resilience"),
        "70_pbv2_matched": payload["pbv2_compare"],
        "71_fcr_only": True,
        "72_pbv2_only": False,
        "73_overlap": "not_computed_pbv2_unchanged",
        "74_cap5": payload["cap5"],
        "75_trades_per_day": cap5.get("trades_per_day"),
        "76_pos_neg": (cap5.get("pos_days"), cap5.get("neg_days")),
        "77_top1_symbol": f5t.get("top1_symbol_share"),
        "78_top3_symbol": None,
        "79_loso": None,
        "80_lodo": None,
        "81_entry_verdict": entry_verdict,
        "82_exit_research": "READY" if vg_ok else "BLOCKED",
        "83_capture_only": True,
        "84_paper": "NO_PAPER_ENTRY",
        "85_live": "LIVE_TRADING_BLOCKED",
        "86_submit": SUBMIT,
        "87_cancel": CANCEL,
        "88_live_order": LIVE_ORDER,
        "89_tests": test_results,
        "90_mainline_changed": False,
        "91_artifacts": str(out_dir),
    }

    print("[fcr] emit…", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
