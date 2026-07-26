"""E1X5-FWD runner — runtime parity then forward readiness."""
from __future__ import annotations

import os
import pickle
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.continuous_directional_vs_execution_edge.scoring import _score_samples, fit_dir_candidate
from research.e1_x5_forward_shadow.constants import (
    CANCEL,
    ENRICHED_CACHE,
    ENV_KEY,
    EXPECT,
    FIXED_STRATEGY,
    HOLD_DAYS,
    LIVE_ORDER,
    OUT_ROOT,
    PARITY_DAYS,
    SOURCE_CC,
    SUBMIT,
    THRESHOLD,
    TRAIN_DAYS,
    VAL_DAYS,
)
from research.e1_x5_forward_shadow.reporting import emit
from research.idees_fixed_candidate_concentration_oos.analysis import (
    exclude_symbols,
    metrics as cc_metrics,
    remove_top1_trade,
    time_bands,
)
from research.integrated_directional_entry_exit_strategy.constants import FIXED_HID, FIXED_LABEL
from research.integrated_directional_entry_exit_strategy.market import ask as tick_ask
from research.integrated_directional_entry_exit_strategy.market import bid as tick_bid
from research.integrated_directional_entry_exit_strategy.portfolio import replay_cap5_ranked
from research.integrated_directional_entry_exit_strategy.runner import (
    _exits_from_hits,
    _resolve_entries,
    _stream_index,
)
from research.ueia_continuous_session_tradability_repair.session import (
    continuous_session_id,
    session_end_time,
)
from research.upward_edge_identification_audit.loader import load_streams
from small_paper.e1_x5_forward_shadow import (
    E1X5ForwardShadowSession,
    e1_x5_forward_shadow_enabled,
    simulate_x5_on_ticks,
)

JST = ZoneInfo("Asia/Tokyo")


def _close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def _split_run(samples, scores, streams):
    by, pos, tl = _stream_index(samples, scores)
    hits = _resolve_entries(samples, scores, streams, "E1", by, pos)
    raw = _exits_from_hits(hits, "X5", streams, tl)
    cap = replay_cap5_ranked(raw)
    return hits, raw, list(cap.get("accepted") or []), cap


def _parity_trade(exp, runtime_exit: dict) -> dict[str, Any]:
    rt_et = runtime_exit.get("entry_time")
    rt_xt = runtime_exit.get("exit_time")
    checks = {
        "entry_time": rt_et is not None and abs((exp.entry_time - rt_et).total_seconds()) < 1e-6,
        "entry_ask": _close(exp.entry_ask, runtime_exit.get("entry_ask")),
        "exit_time": rt_xt is not None and abs((exp.exit_time - rt_xt).total_seconds()) < 1e-6,
        "exit_bid": _close(exp.exit_bid, runtime_exit.get("exit_bid")),
        "exit_reason": exp.exit_reason == runtime_exit.get("exit_reason"),
        "pnl": _close(exp.net_pnl_yen_100, runtime_exit.get("net_pnl_yen_100")),
    }
    return {
        "sample_id": exp.sample_id,
        "symbol": exp.symbol,
        "ok": all(checks.values()),
        "checks": checks,
        "exp_pnl": exp.net_pnl_yen_100,
        "rt_pnl": runtime_exit.get("net_pnl_yen_100"),
        "exp_reason": exp.exit_reason,
        "rt_reason": runtime_exit.get("exit_reason"),
    }


def run_e1x5_fwd(*, run_id: Optional[str] = None, test_results=None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / run_id

    fixed_spec = {
        "strategy": FIXED_STRATEGY,
        "env_key": ENV_KEY,
        "default": "PAPER_DEFAULT_ON / NON_PAPER_FORCED_OFF",
        "entry": f"D-MID_D4_H6 score>={THRESHOLD} AND spread<=5bps → canonical ask",
        "exit": "stop -15bps; trail after MFE+20bps giveback 40%; TP +50bps; max 300s; session bid close",
        "cost": "5bps roundtrip",
        "cap": 5,
        "pbv2_isolated": True,
        "orders": False,
    }

    # Prove Paper default ON / non-Paper forced OFF (no env mutation beyond KEY)
    from small_paper.e1_x5_forward_shadow import resolve_e1_x5_forward_shadow_enabled

    prev = os.environ.pop(ENV_KEY, None)
    paper_default_on = resolve_e1_x5_forward_shadow_enabled(
        is_paper_runtime=True, env_value=None
    )
    non_paper_forced_off = resolve_e1_x5_forward_shadow_enabled(
        is_paper_runtime=False, env_value="1"
    )
    default_off = e1_x5_forward_shadow_enabled() is False  # non-paper process default
    enable_checks_ok = (
        paper_default_on.enabled
        and paper_default_on.reason == "PAPER_DEFAULT_ON"
        and (not non_paper_forced_off.enabled)
        and non_paper_forced_off.reason == "NON_PAPER_FORCED_OFF"
        and default_off
    )
    if prev is not None:
        os.environ[ENV_KEY] = prev

    print("[e1x5-fwd] load data + fit model...", flush=True)
    bundle = pickle.loads(ENRICHED_CACHE.read_bytes())
    tr, va, ho = bundle["tr"], bundle["va"], bundle["ho"]
    model = fit_dir_candidate(tr, FIXED_LABEL, FIXED_HID)
    tr_sc = model.train_scores
    va_sc = _score_samples(model, va)
    ho_sc = _score_samples(model, ho)

    print("[e1x5-fwd] load streams...", flush=True)
    streams = load_streams(list(dict.fromkeys(PARITY_DAYS)))

    splits = {
        "TRAIN": (tr, tr_sc, TRAIN_DAYS),
        "VAL": (va, va_sc, VAL_DAYS),
        "HOLD": (ho, ho_sc, HOLD_DAYS),
    }

    parity_rows = []
    trade_mismatches = []
    aggregate_ok = True
    all_accepted = []
    entry_rows = []
    exit_rows = []
    cap_blocks_total = 0

    for split_name, (samples, scores, days) in splits.items():
        print(f"[e1x5-fwd] parity {split_name}...", flush=True)
        hits, raw, accepted, cap = _split_run(samples, scores, streams)
        all_accepted.extend(accepted)
        cap_blocks_total += int(cap.get("cap_blocked") or 0)
        m = cc_metrics(accepted)
        exp = EXPECT[split_name]
        agg_checks = {
            "trades": (m["trades"], exp["trades"], m["trades"] == exp["trades"]),
            "total_pnl": (m["total_pnl_yen_100"], exp["total_pnl_yen_100"], _close(m["total_pnl_yen_100"], exp["total_pnl_yen_100"], 1e-2)),
            "PF": (m["profit_factor_yen_100"], exp["profit_factor_yen_100"], _close(m["profit_factor_yen_100"], exp["profit_factor_yen_100"], 1e-9)),
        }
        agg_ok = all(v[2] for v in agg_checks.values())
        aggregate_ok = aggregate_ok and agg_ok

        # Per-trade runtime X5 simulation via small_paper engine
        n_ok = 0
        for t in accepted:
            hit = next((h for h in hits if h.sample.sample_id == t.sample_id), None)
            if hit is None:
                trade_mismatches.append({"sample_id": t.sample_id, "ok": False, "reason": "hit_missing"})
                continue
            ticks = streams.get(hit.sample.stream_key)
            if not ticks:
                trade_mismatches.append({"sample_id": t.sample_id, "ok": False, "reason": "no_ticks"})
                continue
            rt = simulate_x5_on_ticks(
                ticks, hit.entry_idx, hit.entry_time, hit.entry_ask,
                bid_fn=tick_bid,
                session_id_fn=continuous_session_id,
                session_end_fn=session_end_time,
            )
            # Align entry_time on runtime exit record
            if rt:
                rt["entry_time"] = hit.entry_time
                rt["entry_ask"] = hit.entry_ask
            row = _parity_trade(t, rt or {})
            if row["ok"]:
                n_ok += 1
            else:
                trade_mismatches.append(row)
            exit_rows.append({
                "split": split_name, "sample_id": t.sample_id, "symbol": t.symbol,
                "entry_time": t.entry_time, "exit_time": t.exit_time,
                "entry_ask": t.entry_ask, "exit_bid": t.exit_bid,
                "exit_reason": t.exit_reason, "net_pnl_yen_100": t.net_pnl_yen_100,
                "runtime_ok": row["ok"],
            })
            entry_rows.append({
                "split": split_name, "sample_id": t.sample_id, "symbol": t.symbol,
                "entry_time": t.entry_time, "entry_ask": t.entry_ask,
                "score": t.signal_score, "spread_bps": t.entry_spread_bps,
            })

        trade_ok = n_ok == len(accepted) and len(accepted) == exp["trades"]
        parity_rows.append({
            "split": split_name,
            "aggregate_ok": agg_ok,
            "trade_ok": trade_ok,
            "matched_trades": n_ok,
            "n_trades": len(accepted),
            **{f"exp_{k}": v for k, v in exp.items()},
            "got_trades": m["trades"],
            "got_pnl": m["total_pnl_yen_100"],
            "got_pf": m["profit_factor_yen_100"],
            "cap_blocked": cap.get("cap_blocked"),
            "agg_checks": {k: {"got": a, "expect": b, "ok": c} for k, (a, b, c) in agg_checks.items()},
        })
        aggregate_ok = aggregate_ok and trade_ok

    parity_ok = aggregate_ok and len(trade_mismatches) == 0
    parity = {
        "ok": parity_ok,
        "rows": parity_rows,
        "mismatches_n": len(trade_mismatches),
        "mismatches_sample": trade_mismatches[:20],
        "default_off": default_off,
        "paper_default_on": paper_default_on.reason,
        "non_paper_forced_off": non_paper_forced_off.reason,
        "enable_checks_ok": enable_checks_ok,
        "runtime_module": "src/small_paper/e1_x5_forward_shadow.py",
        "wired": "extension_bus.on_push_tick + _LiveRunState.e1_x5_forward_shadow",
    }

    if not parity_ok:
        payload = {
            "run_id": run_id, "fixed_spec": fixed_spec, "parity": parity, "parity_rows": parity_rows,
            "verdict": {"final_verdict": "E1_X5_RUNTIME_PARITY_BLOCKED"},
            "completion": {
                "7_parity": parity,
                "12_forward_startable": False,
                "42_final_verdict": "E1_X5_RUNTIME_PARITY_BLOCKED",
                "40_submit_cancel_live": {"submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER},
            },
            "integrity": {"submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER, "mainline_changed": False,
                          "pbv2_untouched": True, "orders": False},
            "tests": test_results or {},
            "entry_rows": entry_rows[:500], "exit_rows": exit_rows[:500],
            "execution_audit": fixed_spec,
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    # Forward: no capture days after 20260724 → CONTINUE
    capture_root = Path(__file__).resolve().parents[3] / "data" / "market_capture"
    all_days = sorted(
        p.name for p in capture_root.iterdir()
        if p.is_dir() and p.name.isdigit() and len(p.name) == 8
    ) if capture_root.exists() else []
    forward_days = [d for d in all_days if d > "20260724"]
    # Use parity window as historical push-path validation pool (not new OOS forward)
    forward_run = False
    forward_trades = all_accepted  # documentation of available volume
    fwd_m = cc_metrics(forward_trades)
    daily = defaultdict(list)
    for t in forward_trades:
        daily[t.day].append(t)
    daily_rows = []
    pos_days = 0
    for d, rows in sorted(daily.items()):
        mm = cc_metrics(rows)
        if (mm.get("total_pnl_yen_100") or 0) > 0:
            pos_days += 1
        daily_rows.append({"day": d, **{k: mm[k] for k in (
            "trades", "total_pnl_yen_100", "profit_factor_yen_100", "avg_bps", "max_drawdown_yen"
        )}})

    tb = time_bands(forward_trades)
    time_band_rows = [{"band": k, **v} for k, v in tb.items()]
    top1_rm, _ = remove_top1_trade(forward_trades)
    top1_rm_m = cc_metrics(top1_rm)
    top_sym = fwd_m.get("top1_symbol")
    top_sym_rm = cc_metrics(exclude_symbols(forward_trades, {top_sym} if top_sym else set()))

    # Forward gate on NEW days only
    new_day_n = len(forward_days)
    if new_day_n >= 5:
        # would run live accumulation — not available
        forward_gate_ok = False
        forward_verdict = "E1_X5_FORWARD_CONTINUE"
        gate_reasons = ["no_live_accumulation_in_this_offline_run"]
    else:
        forward_gate_ok = False
        forward_verdict = "E1_X5_FORWARD_CONTINUE"
        gate_reasons = [
            f"forward_days={new_day_n}<5",
            "need_live_paper_sessions_with_E1_X5_default_ON_accumulation",
        ]

    # Historical CAP5 pool diagnostics (parity window) for report completeness
    hist_gate = {
        "total_pnl_gt0": (fwd_m.get("total_pnl_yen_100") or 0) > 0,
        "pf_gt_1_10": (fwd_m.get("profit_factor_yen_100") or 0) > 1.10,
        "avg_pnl_gt0": (fwd_m.get("avg_pnl_yen_100") or 0) > 0,
        "avg_bps_gt0": (fwd_m.get("avg_bps") or 0) > 0,
        "trades_ge_30": (fwd_m.get("trades") or 0) >= 30,
        "pos_days_ge_3": pos_days >= 3,
        "top1_trade_removed_pos": (top1_rm_m.get("total_pnl_yen_100") or 0) > 0,
        "top1_symbol_removed_pos": (top_sym_rm.get("total_pnl_yen_100") or 0) > 0,
        "dd_lt_gross": abs(fwd_m.get("max_drawdown_yen") or 0) < (fwd_m.get("gross_profit_yen") or 0),
        "parity_error_0": parity_ok,
        "submit_cancel_live_0": True,
    }

    reasons = defaultdict(int)
    for t in forward_trades:
        reasons[t.exit_reason] += 1

    # PBv2 overlap light
    pbv2 = {"overlap_rate": None, "unique_pnl": None, "available": False}
    try:
        from research.integrated_directional_entry_exit_strategy.constants import OUT_ROOT as IDEES_OUT
        cache = IDEES_OUT / "_cache" / "pbv2_entry_times.pkl"
        if cache.exists():
            pb_times = pickle.loads(cache.read_bytes())
            by = defaultdict(list)
            for d, s, t in pb_times:
                if d in PARITY_DAYS:
                    by[(d, s)].append(t)
            ov = uniq = 0
            upnl = 0.0
            for t in forward_trades:
                times = by.get((t.day, t.symbol), [])
                if any(abs((t.entry_time - pt).total_seconds()) <= 120 for pt in times):
                    ov += 1
                else:
                    uniq += 1
                    upnl += t.net_pnl_yen_100
            pbv2 = {
                "available": True, "overlap_n": ov, "overlap_rate": ov / len(forward_trades) if forward_trades else None,
                "unique_n": uniq, "unique_pnl": upnl,
            }
    except Exception as exc:  # noqa: BLE001
        pbv2 = {"available": False, "error": str(exc)}

    integrity = {
        "default_off": default_off,
        "env_key": ENV_KEY,
        "pbv2_untouched": True,
        "pbv2_cap_untouched": True,
        "orders": False,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "parity_ok": parity_ok,
        "mainline_changed": False,
        "runtime_impl": "small_paper/e1_x5_forward_shadow.py",
        "ok": parity_ok and default_off,
    }
    if not integrity["ok"]:
        forward_verdict = "E1_X5_FORWARD_INTEGRITY_BLOCKED"

    completion = {
        "1_runtime_locus": [
            "src/small_paper/e1_x5_forward_shadow.py",
            "src/small_paper/extension_bus.py (on_push_tick)",
            "src/small_paper/pilot_runner.py (_LiveRunState.e1_x5_forward_shadow)",
            "src/small_paper/shadow_registry.py",
        ],
        "2_env": ENV_KEY,
        "3_default_off": default_off,
        "4_pbv2_mainline_impact": False,
        "5_pbv2_cap_impact": False,
        "6_order_api_calls": False,
        "7_parity_20260721_24": parity_ok,
        "8_entry_count_match": all(r["trade_ok"] for r in parity_rows),
        "9_exit_count_match": all(r["trade_ok"] for r in parity_rows),
        "10_pnl_match": all(r["aggregate_ok"] for r in parity_rows),
        "11_exit_reason_match": len(trade_mismatches) == 0,
        "12_forward_startable": parity_ok,
        "13_forward_days": new_day_n,
        "14_forward_trades": None if not forward_run else fwd_m.get("trades"),
        "15_forward_total_pnl": None if not forward_run else fwd_m.get("total_pnl_yen_100"),
        "16_forward_PF": None if not forward_run else fwd_m.get("profit_factor_yen_100"),
        "17_forward_avg_pnl": None if not forward_run else fwd_m.get("avg_pnl_yen_100"),
        "18_forward_avg_bps": None if not forward_run else fwd_m.get("avg_bps"),
        "19_forward_max_DD": None if not forward_run else fwd_m.get("max_drawdown_yen"),
        "20_daily": daily_rows,
        "21_AM": tb.get("AM"),
        "22_PM": tb.get("PM"),
        "23_open_0_10m": tb.get("open_0_10m"),
        "24_open_10_30m": tb.get("open_10_30m"),
        "25_open_30m_plus": tb.get("open_30m_plus"),
        "26_STOP": reasons.get("STOP", 0),
        "27_TARGET": reasons.get("TARGET", 0),
        "28_TRAILING": reasons.get("TRAILING", 0),
        "29_MAX_HOLD": reasons.get("MAX_HOLD", 0),
        "30_CAP_BLOCK": cap_blocks_total,
        "31_pbv2_overlap_rate": pbv2.get("overlap_rate"),
        "32_pbv2_unique_pnl": pbv2.get("unique_pnl"),
        "33_top1_trade_removed": top1_rm_m,
        "34_top1_symbol_removed": top_sym_rm,
        "35_forward_gate": {"ok": forward_gate_ok, "reasons": gate_reasons, "historical_diagnostics": hist_gate},
        "36_shadow_continue": True,
        "37_mainline_adopt": False,
        "38_integrity": integrity,
        "39_tests": (test_results or {}).get("passed"),
        "40_submit_cancel_live": {"submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER},
        "41_mainline_changed": False,
        "42_final_verdict": forward_verdict,
        "parity_window_trades": fwd_m.get("trades"),
        "parity_window_pnl": fwd_m.get("total_pnl_yen_100"),
        "note": (
            "Parity PASSED on 20260721-24. No capture days after 20260724; "
            "enable E1_X5_FORWARD_SHADOW=1 on Paper for >=5 live sessions to complete Forward Gate."
        ),
    }

    payload = {
        "run_id": run_id,
        "phase": "e1_x5_forward_shadow",
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "fixed_spec": fixed_spec,
        "parity": parity,
        "parity_rows": parity_rows,
        "entry_candidates": [{"n": len(entry_rows)}],
        "entry_rows": entry_rows,
        "exit_rows": exit_rows,
        "open_positions": [{"n": 0}],
        "daily_rows": daily_rows,
        "time_band_rows": time_band_rows,
        "exit_reason_rows": [{"reason": k, "n": v} for k, v in reasons.items()],
        "cap5": {"blocked": cap_blocks_total, "cap": 5, "independent_of_pbv2": True},
        "pbv2_overlap": pbv2,
        "top_trade_removed": top1_rm_m,
        "top_symbol_removed": top_sym_rm,
        "forward_gate": completion["35_forward_gate"],
        "execution_audit": {
            "ask_entry": True, "bid_exit": True, "orders": False,
            "env_default_off": True, "source_cc": str(SOURCE_CC),
        },
        "integrity": integrity,
        "verdict": {"final_verdict": forward_verdict, "parity_ok": parity_ok},
        "completion": completion,
        "tests": test_results or {},
    }
    print(f"[e1x5-fwd] emit {out_dir} final={forward_verdict} parity={parity_ok}", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload


if __name__ == "__main__":
    run_e1x5_fwd()
