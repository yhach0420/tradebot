"""Canonical VCIE exact-method runner."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_vcie_exact_method.arms import (
    collect_all_arms,
    fit_thresholds_train,
    train_gate,
    val_gate,
)
from research.canonical_vcie_exact_method.constants import (
    CANCEL,
    LIVE_ORDER,
    OUT_ROOT,
    SEED,
    SOT_AUDIT,
    SOT_EGC,
    SOT_REPAIR,
    SOT_V2,
    SUBMIT,
)
from research.canonical_vcie_exact_method.data_split import discover_and_split
from research.canonical_vcie_exact_method.execution import evaluate_execution
from research.canonical_vcie_exact_method.lineage import audit_lineage
from research.canonical_vcie_exact_method.loader import load_streams
from research.canonical_vcie_exact_method.opportunity import evaluate_candidates, incremental
from research.canonical_vcie_exact_method.reporting import emit

JST = ZoneInfo("Asia/Tokyo")


def _summ(ev: dict[str, Any]) -> dict[str, Any]:
    return {k: ev.get(k) for k in ("n", "pnl", "pf", "never_rate", "early_adverse_rate", "winner_rate", "avg_mfe", "avg_mae", "mean", "top1_symbol_share")}


def run_vcie(
    *,
    run_id: Optional[str] = None,
    stride: int = 4,
    out_root: Optional[Path] = None,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id
    print("[vcie] split…", flush=True)
    discovery = discover_and_split()
    days = list(dict.fromkeys(
        (discovery.get("warmup") or [])
        + (discovery.get("train") or [])
        + (discovery.get("validation") or [])
        + (discovery.get("strict_oos") or [])
    ))
    print(f"[vcie] load days={days} stride={stride}", flush=True)
    streams = load_streams(days, stride=stride)
    print(f"[vcie] streams={len(streams)} ticks={sum(len(v) for v in streams.values())}", flush=True)

    lineage = audit_lineage(streams, discovery)
    if lineage["any_blocked"]:
        payload = _blocked_payload(run_id, discovery, lineage, streams, test_results, out_dir)
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    train_days = discovery["train"]
    val_days = discovery["validation"]
    oos_days = discovery["strict_oos"]
    insufficient = bool(discovery.get("insufficient_oos"))

    print("[vcie] fit thresholds TRAIN (one-factor-at-a-time)…", flush=True)
    thr = fit_thresholds_train(streams, train_days)
    print(f"[vcie] thresholds={ {k: thr[k] for k in thr if k != 'diagnostics'} }", flush=True)

    def eval_split(days_list: list[str]) -> tuple[dict, dict, Any]:
        arms = collect_all_arms(
            streams, days_list,
            vol_ratio=thr["vol_ratio"],
            buy_ratio=thr["buy_ratio"],
            hold_mode=thr["hold_mode"],
            hold_n=thr["hold_n"],
            expiry_sec=thr["expiry_sec"],
            spread_max_bps=thr["spread_max_bps"],
        )
        eps = arms.pop("_episodes")
        d1e = arms.pop("_d1_episodes")
        results = {}
        for name in ("V1_PRICE_CROSS", "V2_VOLUME_CONFIRMED", "V3_TRADE_SIDE_CONFIRMED", "V4_FULL_VCIE", "D1_PRICE_PLUS_TRADE_SIDE"):
            results[name] = evaluate_candidates(arms[name], streams)
        return results, arms, (eps, d1e)

    print("[vcie] TRAIN arms…", flush=True)
    train_res, train_arms, (train_eps, _) = eval_split(train_days)
    print("[vcie] VALIDATION arms…", flush=True)
    val_res, val_arms, _ = eval_split(val_days)
    print("[vcie] OOS arms…", flush=True)
    oos_res, oos_arms, (oos_eps, _) = eval_split(oos_days)

    inc = {
        "V1_to_V2": incremental(train_res["V1_PRICE_CROSS"], train_res["V2_VOLUME_CONFIRMED"]),
        "V2_to_V3": incremental(train_res["V2_VOLUME_CONFIRMED"], train_res["V3_TRADE_SIDE_CONFIRMED"]),
        "V3_to_V4": incremental(train_res["V3_TRADE_SIDE_CONFIRMED"], train_res["V4_FULL_VCIE"]),
        "D1_to_V3": incremental(train_res["D1_PRICE_PLUS_TRADE_SIDE"], train_res["V3_TRADE_SIDE_CONFIRMED"]),
    }

    tg_ok, tg_reason = train_gate(train_res["V4_FULL_VCIE"], train_res["V1_PRICE_CROSS"], train_res["V3_TRADE_SIDE_CONFIRMED"])
    vg_ok, vg_reason = (False, "SKIPPED_NO_TRAIN")
    if tg_ok:
        vg_ok, vg_reason = val_gate(val_res["V4_FULL_VCIE"])

    # episode stats
    def ep_stats(eps):
        return {
            "n": len(eps),
            "expired": sum(1 for e in eps if e.status == "EXPIRED"),
            "failed": sum(1 for e in eps if e.status == "FAILED"),
            "entry_ready": sum(1 for e in eps if e.status == "ENTRY_READY"),
            "crosses": sum(1 for e in eps if e.cross_idx is not None),
            "bursts": sum(1 for e in eps if e.burst_idx is not None),
            "sides": sum(1 for e in eps if e.side_idx is not None),
            "holds": sum(1 for e in eps if e.has_hold),
        }

    est = ep_stats(train_eps)
    event_counts = {
        "context_events": sum(1 for e in train_eps if e.context_type in ("HOLD", "CONTROLLED_PULLBACK")),
        "volume_bursts": est["bursts"],
        "trade_side_confirmations": est["sides"],
        "price_crosses": est["crosses"],
        "breakout_holds": est["holds"],
        "full_vcie_entry_ready": est["entry_ready"],
    }

    # execution on V4 candidates (train+val union for coverage; OOS if validated)
    exec_cands = train_arms["V4_FULL_VCIE"] + val_arms["V4_FULL_VCIE"]
    if vg_ok:
        exec_cands = oos_arms["V4_FULL_VCIE"] or exec_cands
    execution = evaluate_execution(exec_cands, streams, hold_sec=120.0)

    # CAP5 only if validated
    cap5 = {"trades": 0, "pnl_5bps": 0.0, "note": "no_validated_candidate"}
    if vg_ok and oos_arms["V4_FULL_VCIE"]:
        from research.canonical_zero_base_v2.cap5 import CapTrade, replay_cap5
        from research.canonical_vcie_exact_method.opportunity import path_metrics

        trades = []
        for c in oos_arms["V4_FULL_VCIE"]:
            ticks = streams[c.stream_key]
            m = path_metrics(ticks, c.entry_idx, c.entry_ask, max_sec=120)
            if not m.get("evaluable"):
                continue
            # fixed horizon exit bid
            t0 = ticks[c.entry_idx].ts
            exit_bid = c.entry_ask
            exit_t = t0
            for j in range(c.entry_idx + 1, len(ticks)):
                if (ticks[j].ts - t0).total_seconds() >= 120:
                    b = ticks[j].board.canonical_best_bid
                    if b:
                        exit_bid = float(b)
                        exit_t = ticks[j].ts
                    break
            trades.append(CapTrade(
                day=c.day, symbol=c.symbol, episode_id=c.episode_id,
                entry_time=c.entry_time, exit_time=exit_t,
                entry_price=c.entry_ask, exit_price=exit_bid,
                pnl_5bps=float(m["terminal_pnl_yen"]), exit_reason="fixed_horizon_R0",
                strategy_id="VCIE", setup_id=c.episode_id,
                session="AM" if c.entry_time.hour < 12 else "PM",
                mfe=float(m["mfe"]), mae=float(m["mae"]),
                winner=bool(m["winner"]),
            ))
        cap5 = replay_cap5(trades, portfolio_id="VCIE_CAP5")

    # judgments
    codes = []
    for k, block_name in (
        ("volume", "VOLUME_LINEAGE"),
        ("trade_direction", "TRADE_DIRECTION_LINEAGE"),
        ("session_time", "SESSION_TIME_LINEAGE"),
        ("execution", "CANONICAL_EXECUTION_LINEAGE"),
    ):
        codes.append(lineage[k]["verdict"])
    codes.append("V1_PRICE_CROSS_EVALUATED")
    codes.append("V2_VOLUME_INCREMENT_POSITIVE" if inc["V1_to_V2"].get("positive_effect") else "V2_VOLUME_INCREMENT_NEGATIVE")
    codes.append("V3_TRADE_SIDE_INCREMENT_POSITIVE" if inc["V2_to_V3"].get("positive_effect") else "V3_TRADE_SIDE_INCREMENT_NEGATIVE")
    codes.append("V4_HOLD_INCREMENT_POSITIVE" if inc["V3_to_V4"].get("positive_effect") else "V4_HOLD_INCREMENT_NEGATIVE")

    if not tg_ok:
        entry_verdict = "NO_TRAIN_CANONICAL_VCIE_CANDIDATE"
        codes.append(entry_verdict)
        codes.append("CANONICAL_VCIE_ENTRY_NO_EDGE")
        codes.append("VCIE_EXIT_RESEARCH_BLOCKED")
    elif not vg_ok:
        entry_verdict = "NO_VALIDATED_CANONICAL_VCIE_CANDIDATE"
        codes.append(entry_verdict)
        codes.append("CANONICAL_VCIE_ENTRY_NO_EDGE")
        codes.append("VCIE_EXIT_RESEARCH_BLOCKED")
    else:
        entry_verdict = "CANONICAL_VCIE_ENTRY_CANDIDATE"
        codes.append(entry_verdict)
        codes.append("VCIE_EXIT_RESEARCH_READY")

    codes.append(execution.get("resilience") or "EXECUTION_FRAGILE")
    if execution.get("EXECUTION_RESOLUTION_BLOCKED"):
        codes.append("EXECUTION_RESOLUTION_BLOCKED")
    codes.extend(["INSUFFICIENT_CANONICAL_OOS", "CAPTURE_ONLY_CONTINUE", "NO_PAPER_ENTRY", "NO_PRODUCTION_CHANGE", "LIVE_TRADING_BLOCKED"])

    final_verdict = "INSUFFICIENT_CANONICAL_OOS"
    if lineage["any_blocked"]:
        final_verdict = "CANONICAL_VCIE_DATA_LINEAGE_BLOCKED"
    elif entry_verdict.startswith("NO_"):
        final_verdict = "INSUFFICIENT_CANONICAL_OOS"  # still primary under 4-day constraint
        # keep entry_verdict in codes

    v4t = train_res["V4_FULL_VCIE"]
    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "canonical_vcie_exact_method",
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
            "sot_v2_ref_only": str(SOT_V2),
            "method": "yesterday_incremental_V1_V2_V3_V4",
            "forbidden": ["broad_feature_search", "T0_T9", "Z1_Z4", "board_confirmation_arm", "old_thresholds"],
        },
        "split": discovery,
        "lineage": lineage,
        "thresholds": {k: thr[k] for k in thr if k != "diagnostics"},
        "threshold_diagnostics": thr.get("diagnostics"),
        "event_counts": event_counts,
        "episode_stats": est,
        "train_results": {k: _summ(v) for k, v in train_res.items()},
        "val_results": {k: _summ(v) for k, v in val_res.items()},
        "oos_results": {k: _summ(v) for k, v in oos_res.items()},
        "arm_results": {
            "V1_PRICE_CROSS": {"train": _summ(train_res["V1_PRICE_CROSS"]), "val": _summ(val_res["V1_PRICE_CROSS"]), "oos": _summ(oos_res["V1_PRICE_CROSS"])},
            "V2_VOLUME_CONFIRMED": {"train": _summ(train_res["V2_VOLUME_CONFIRMED"]), "val": _summ(val_res["V2_VOLUME_CONFIRMED"]), "oos": _summ(oos_res["V2_VOLUME_CONFIRMED"])},
            "V3_TRADE_SIDE_CONFIRMED": {"train": _summ(train_res["V3_TRADE_SIDE_CONFIRMED"]), "val": _summ(val_res["V3_TRADE_SIDE_CONFIRMED"]), "oos": _summ(oos_res["V3_TRADE_SIDE_CONFIRMED"])},
            "V4_FULL_VCIE": {"train": _summ(train_res["V4_FULL_VCIE"]), "val": _summ(val_res["V4_FULL_VCIE"]), "oos": _summ(oos_res["V4_FULL_VCIE"])},
            "D1_PRICE_PLUS_TRADE_SIDE": {"train": _summ(train_res["D1_PRICE_PLUS_TRADE_SIDE"]), "val": _summ(val_res["D1_PRICE_PLUS_TRADE_SIDE"]), "oos": _summ(oos_res["D1_PRICE_PLUS_TRADE_SIDE"])},
        },
        "incremental": inc,
        "train_gate": {"ok": tg_ok, "reason": tg_reason},
        "val_gate": {"ok": vg_ok, "reason": vg_reason},
        "execution": execution,
        "cap5": {k: cap5.get(k) for k in ("trades", "pnl_5bps", "PF_5bps", "trades_per_day", "pos_days", "neg_days", "note") if k in cap5 or True},
        "cap_blocked": {"note": "n/a" if vg_ok else "no_validated_candidate"},
        "daily_results": cap5.get("daily_pnl"),
        "symbol_results": cap5.get("top_symbols"),
        "dependency": {"top1_symbol_share": v4t.get("top1_symbol_share")},
        "opportunity_note": {"entry": "E1 canonical Ask", "future": "canonical Bid", "cost_bps": 5},
        "verdict": {
            "final_verdict": final_verdict,
            "entry_verdict": entry_verdict,
            "codes": codes,
            "INSUFFICIENT_CANONICAL_OOS": insufficient,
            "CAPTURE_ONLY_CONTINUE": True,
            "NO_PAPER_ENTRY": True,
            "NO_PRODUCTION_CHANGE": True,
            "LIVE_TRADING_BLOCKED": True,
            "VCIE_EXIT_RESEARCH_READY": vg_ok,
            "VCIE_EXIT_RESEARCH_BLOCKED": not vg_ok,
        },
        "tests": test_results or {"all_passed": False, "rows": [{"name": "deferred", "status": "pending"}]},
    }

    payload["completion"] = {
        "1_final_verdict": final_verdict,
        "2_volume_lineage": lineage["volume"]["verdict"],
        "3_trade_direction_lineage": lineage["trade_direction"]["verdict"],
        "4_session_time_lineage": lineage["session_time"]["verdict"],
        "5_canonical_execution_lineage": lineage["execution"]["verdict"],
        "6_canonical_days": discovery.get("eligible_days"),
        "7_warmup": discovery.get("warmup"),
        "8_train": train_days,
        "9_validation": val_days,
        "10_strict_oos": oos_days,
        "11_raw_events": sum(len(v) for v in streams.values()),
        "12_context_events": event_counts["context_events"],
        "13_volume_bursts": event_counts["volume_bursts"],
        "14_trade_side_confirmations": event_counts["trade_side_confirmations"],
        "15_price_crosses": event_counts["price_crosses"],
        "16_breakout_holds": event_counts["breakout_holds"],
        "17_full_vcie_entry_ready": event_counts["full_vcie_entry_ready"],
        "18_expired_episodes": est["expired"],
        "19_failed_episodes": est["failed"],
        "20_V1_n": train_res["V1_PRICE_CROSS"].get("n"),
        "21_V2_n": train_res["V2_VOLUME_CONFIRMED"].get("n"),
        "22_V3_n": train_res["V3_TRADE_SIDE_CONFIRMED"].get("n"),
        "23_V4_n": train_res["V4_FULL_VCIE"].get("n"),
        "24_D1_n": train_res["D1_PRICE_PLUS_TRADE_SIDE"].get("n"),
        "25_V1_train": _summ(train_res["V1_PRICE_CROSS"]),
        "26_V2_train": _summ(train_res["V2_VOLUME_CONFIRMED"]),
        "27_V3_train": _summ(train_res["V3_TRADE_SIDE_CONFIRMED"]),
        "28_V4_train": _summ(train_res["V4_FULL_VCIE"]),
        "29_V1_to_V2": inc["V1_to_V2"],
        "30_V2_to_V3": inc["V2_to_V3"],
        "31_V3_to_V4": inc["V3_to_V4"],
        "32_D1_to_V3": inc["D1_to_V3"],
        "33_train_pass": tg_ok,
        "34_val_V1_V4": {k: _summ(val_res[k]) for k in val_res},
        "35_val_pass": vg_ok,
        "36_oos_carry": bool(vg_ok),
        "37_final_entry": {
            "arm": "V4_FULL_VCIE" if tg_ok else None,
            "thresholds": {k: thr[k] for k in thr if k != "diagnostics"},
        } if tg_ok else None,
        "38_causal_order": "context→volume_burst→trade_side→price_cross→hold→ENTRY",
        "39_volume_threshold": thr["vol_ratio"],
        "40_trade_side_threshold": thr["buy_ratio"],
        "41_hold": {"mode": thr["hold_mode"], "n": thr["hold_n"]},
        "42_expiry": thr["expiry_sec"],
        "43_spread": thr["spread_max_bps"],
        "44_opportunity_pnl": v4t.get("pnl"),
        "45_opportunity_pf": v4t.get("pf"),
        "46_never_profitable": v4t.get("never_rate"),
        "47_early_adverse": v4t.get("early_adverse_rate"),
        "48_winner_rate": v4t.get("winner_rate"),
        "49_mfe": v4t.get("avg_mfe"),
        "50_mae": v4t.get("avg_mae"),
        "51_cost_recovery": "see opportunity rows",
        "52_E1": (execution.get("E0_E5") or {}).get("E1"),
        "53_E2": (execution.get("E0_E5") or {}).get("E2"),
        "54_E4": (execution.get("E0_E5") or {}).get("E4"),
        "55_1tick_adverse": execution.get("one_tick_adverse"),
        "56_execution_resilience": execution.get("resilience"),
        "57_cap5": payload["cap5"],
        "58_trades_per_day": cap5.get("trades_per_day"),
        "59_pos_neg_days": (cap5.get("pos_days"), cap5.get("neg_days")),
        "60_top1_symbol": v4t.get("top1_symbol_share"),
        "61_top3_symbol": None,
        "62_loso": None,
        "63_lodo": None,
        "64_entry_verdict": entry_verdict,
        "65_exit_research": "READY" if vg_ok else "BLOCKED",
        "66_capture_only": True,
        "67_paper": "NO_PAPER_ENTRY",
        "68_live": "LIVE_TRADING_BLOCKED",
        "69_submit": SUBMIT,
        "70_cancel": CANCEL,
        "71_live_order": LIVE_ORDER,
        "72_tests": test_results,
        "73_mainline_changed": False,
        "74_artifacts": str(out_dir),
    }

    print("[vcie] emit…", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload


def _blocked_payload(run_id, discovery, lineage, streams, test_results, out_dir) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_changed": False,
        "paper_auto_start": False,
        "live_trading_enabled": False,
        "split": discovery,
        "lineage": lineage,
        "verdict": {
            "final_verdict": "CANONICAL_VCIE_DATA_LINEAGE_BLOCKED",
            "CAPTURE_ONLY_CONTINUE": True,
            "NO_PAPER_ENTRY": True,
            "NO_PRODUCTION_CHANGE": True,
            "LIVE_TRADING_BLOCKED": True,
            "codes": [lineage[k]["verdict"] for k in ("volume", "trade_direction", "session_time", "execution")],
        },
        "tests": test_results or {},
        "completion": {
            "1_final_verdict": "CANONICAL_VCIE_DATA_LINEAGE_BLOCKED",
            "2_volume_lineage": lineage["volume"]["verdict"],
            "3_trade_direction_lineage": lineage["trade_direction"]["verdict"],
            "4_session_time_lineage": lineage["session_time"]["verdict"],
            "5_canonical_execution_lineage": lineage["execution"]["verdict"],
            "11_raw_events": sum(len(v) for v in streams.values()),
            "66_capture_only": True,
            "67_paper": "NO_PAPER_ENTRY",
            "74_artifacts": str(out_dir),
        },
        "arm_results": {},
        "train_results": {},
        "val_results": {},
        "oos_results": {},
        "incremental": {},
        "execution": {},
        "cap5": {},
        "event_counts": {},
        "episode_stats": {},
        "thresholds": {},
        "source_audit": {},
        "opportunity_note": {},
        "dependency": {},
        "cap_blocked": {},
        "daily_results": {},
        "symbol_results": {},
    }
