"""Frozen confirmation re-eval with execution-grade Ask/Bid fills."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from research.eec_confirmation_integrity.causal import audit_candidate
from research.eec_confirmation_integrity.constants import FROZEN_NOISE
from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import path_for_contract, simulate_matched_exit
from research.eec_noise_hysteresis.path_util import path_with_lookback
from research.execution_grade_confirmation.board import AtomicQuote, crossed_audit, load_quotes_for_symbols, sym_norm
from research.execution_grade_confirmation.constants import (
    CAP,
    ENTRY_LABELS,
    EXIT_LABELS,
    MAX_CROSSED_RATE,
    MIN_ASK_COVERAGE,
    MIN_BID_COVERAGE,
)
from research.execution_grade_confirmation.fills import entry_fill, exit_fill, trade_pnl
from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit_integrity.dd import summarize_dd
from research.price_flow_exit_integrity.dependency import dependency_audit
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def decision_quote(quotes: Sequence[AtomicQuote], confirm_t: datetime) -> Optional[AtomicQuote]:
    """Map 1s confirmation bar time → last atomic event in that second (same-payload decision)."""
    t0 = confirm_t.replace(microsecond=0)
    t1 = t0 + timedelta(seconds=1)
    last = None
    # linear from binary start
    lo, hi = 0, len(quotes)
    while lo < hi:
        mid = (lo + hi) // 2
        if quotes[mid].received_at < t0:
            lo = mid + 1
        else:
            hi = mid
    for q in quotes[lo:]:
        if q.received_at >= t1:
            break
        if q.received_at >= t0:
            last = q
    return last


def freeze_strict_confirmations(
    contracts: Sequence[EntryContract],
    push: dict,
    *,
    oos_days: Sequence[str],
) -> list[dict[str, Any]]:
    oos = [c for c in contracts if c.day in oos_days]
    rows = []
    for c in oos:
        ticks = (push.get(c.day) or {}).get(c.symbol) or []
        if not ticks:
            continue
        a = audit_candidate(c, ticks, peers=oos, noise=FROZEN_NOISE)
        if a.get("causal_ok"):
            a["contract"] = c  # keep for exit timing
            rows.append(a)
    return rows


def _exit_decision_time(c: EntryContract, ticks: Sequence[PushTick], confirm_t: datetime, entry_px: float) -> Optional[datetime]:
    path, _ = path_with_lookback(ticks, confirm_t)
    path_post = [b for b in path if b.t >= confirm_t]
    if not path_post:
        path0 = path_for_contract(c, ticks)
        path_post = [b for b in path0 if b.t >= confirm_t]
    if not path_post:
        return None
    c2 = replace(c, entry_time=confirm_t, entry_price=entry_px)
    ex = simulate_matched_exit(c2, path_post)
    return ex.exit_time


def evaluate_confirmations(
    frozen: Sequence[dict[str, Any]],
    quotes_by_day: dict[str, dict[str, list[AtomicQuote]]],
    push: dict,
) -> dict[str, Any]:
    entry_rows = {e: [] for e in ENTRY_LABELS}
    exit_rows = {x: [] for x in EXIT_LABELS}
    pair_rows = []  # E1+X1 default pair for gate
    n = len(frozen)
    ask_ok = bid_ok = 0

    for a in frozen:
        day, sym = a["day"], sym_norm(a["symbol"])
        quotes = (quotes_by_day.get(day) or {}).get(sym) or []
        if not quotes or not a.get("confirmation_time"):
            for e in ENTRY_LABELS:
                entry_rows[e].append({"setup_id": a["setup_id"], "fill_status": "NOT_EVALUABLE", "reason": "no_quotes"})
            continue
        ct = datetime.fromisoformat(a["confirmation_time"])
        dq = decision_quote(quotes, ct)
        decision_t = dq.received_at if dq is not None else ct

        fills_e = {}
        for e in ENTRY_LABELS:
            if e == "E0":
                # force decision event time for same-payload
                fr = entry_fill(quotes, decision_t, scenario="E0")
            else:
                fr = entry_fill(quotes, decision_t, scenario=e)
            fr.update(
                {
                    "setup_id": a["setup_id"],
                    "episode_id": a["episode_id"],
                    "day": day,
                    "symbol": sym,
                    "session": a.get("session"),
                    "confirmation_time": a["confirmation_time"],
                    "decision_time_used": decision_t.isoformat(),
                }
            )
            entry_rows[e].append(fr)
            fills_e[e] = fr
        if fills_e["E1"].get("fill_status") == "FILLED":
            ask_ok += 1

        # EXIT decision from matched EC2 timing (unchanged rules)
        c: EntryContract = a["contract"]
        ticks = (push.get(day) or {}).get(c.symbol) or []
        entry_px_proxy = fills_e["E1"].get("fill_price") or fills_e["E0"].get("fill_price") or c.entry_price
        xt = _exit_decision_time(c, ticks, ct, float(entry_px_proxy))
        if xt is None:
            for x in EXIT_LABELS:
                exit_rows[x].append({"setup_id": a["setup_id"], "fill_status": "NOT_EVALUABLE", "reason": "no_exit_time"})
            continue
        xq = decision_quote(quotes, xt)
        x_decision = xq.received_at if xq is not None else xt
        fills_x = {}
        for x in EXIT_LABELS:
            if x == "X0":
                xr = exit_fill(quotes, x_decision, scenario="X0")
            else:
                xr = exit_fill(quotes, x_decision, scenario=x)
            xr.update(
                {
                    "setup_id": a["setup_id"],
                    "day": day,
                    "symbol": sym,
                    "exit_decision_time": xt.isoformat(),
                    "decision_time_used": x_decision.isoformat(),
                }
            )
            exit_rows[x].append(xr)
            fills_x[x] = xr
        if fills_x["X1"].get("fill_status") == "FILLED":
            bid_ok += 1

        # primary pair E1/X1 for coverage; also store E0/X0
        pair_rows.append(
            {
                "setup_id": a["setup_id"],
                "episode_id": a["episode_id"],
                "day": day,
                "symbol": sym,
                "session": a.get("session"),
                "E0": fills_e["E0"],
                "E1": fills_e["E1"],
                "X0": fills_x["X0"],
                "X1": fills_x["X1"],
                "fills_e": fills_e,
                "fills_x": fills_x,
            }
        )

    ask_cov = ask_ok / max(1, n)
    bid_cov = bid_ok / max(1, n)
    return {
        "n_strict": n,
        "ask_coverage_E1": round(ask_cov, 4),
        "bid_coverage_X1": round(bid_cov, 4),
        "ask_filled_n": ask_ok,
        "bid_filled_n": bid_ok,
        "entry_rows": entry_rows,
        "exit_rows": exit_rows,
        "pair_rows": pair_rows,
    }


def reconstruction_gate(eval_pack: dict[str, Any], cross_audit: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    ask_cov = float(eval_pack.get("ask_coverage_E1") or 0)
    bid_cov = float(eval_pack.get("bid_coverage_X1") or 0)
    crossed = float(cross_audit.get("crossed_rate_true_book") or 0)
    mapping_ok = bool(lineage.get("field_mapping_pass"))
    atomic_ok = lineage.get("raw_board_atomic") == "RAW_BOARD_ATOMIC_AVAILABLE"
    mono_ok = int(cross_audit.get("non_monotonic_timestamp") or 0) / max(1, int(cross_audit.get("total_board_events") or 1)) < 0.01
    same_event = lineage.get("quote_lineage") == "QUOTE_LINEAGE_PASS"
    ready = (
        ask_cov >= MIN_ASK_COVERAGE
        and bid_cov >= MIN_BID_COVERAGE
        and crossed <= MAX_CROSSED_RATE
        and mapping_ok
        and atomic_ok
        and mono_ok
        and same_event
    )
    return {
        "ready": ready,
        "verdict": "HISTORICAL_EXECUTION_RECONSTRUCTION_READY" if ready else "HISTORICAL_EXECUTION_RECONSTRUCTION_BLOCKED",
        "ask_coverage": ask_cov,
        "bid_coverage": bid_cov,
        "crossed_rate": crossed,
        "field_mapping_pass": mapping_ok,
        "timestamp_monotonic_pass": mono_ok,
        "same_event_lineage_pass": same_event,
        "raw_atomic": atomic_ok,
        "gates": {
            "ask_coverage_ge_80": ask_cov >= MIN_ASK_COVERAGE,
            "bid_coverage_ge_80": bid_cov >= MIN_BID_COVERAGE,
            "crossed_le_5pct": crossed <= MAX_CROSSED_RATE,
            "field_mapping": mapping_ok,
            "monotonic": mono_ok,
            "lineage": same_event,
        },
    }


def _summarize_trades(trades: list[SimTrade], *, oos_days: Sequence[str], mode: str) -> dict[str, Any]:
    pnls = [t.pnl_5bps for t in trades]
    block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
    dep = dependency_audit(trades, label=mode) if trades else {"dependency_blocked": False}
    dd = summarize_dd(trades) if trades else {}
    kept, _ = filter_no_overlap(sorted(trades, key=lambda t: (t.entry_time, t.setup_id)))
    cap = replay_cap5(kept, portfolio_id=mode, cap=CAP)
    by_day = defaultdict(float)
    for t in trades:
        by_day[t.day] += t.pnl_5bps
    return {
        "mode": mode,
        "n_traded": len(trades),
        **block,
        "trades_per_day": round(len(trades) / max(1, len(oos_days)), 2),
        "pos_days": sum(1 for v in by_day.values() if v > 0),
        "neg_days": sum(1 for v in by_day.values() if v < 0),
        "dependency": dep,
        "dependency_blocked": bool(dep.get("dependency_blocked")),
        "dd_trade_sequence_max_dd": dd.get("trade_sequence_max_dd"),
        "cap5": cap.summary(),
        "day_pnl": {k: round(v, 2) for k, v in by_day.items()},
    }


def build_pair_trades(
    pair_rows: Sequence[dict],
    *,
    entry_sc: str,
    exit_sc: str,
) -> list[SimTrade]:
    trades = []
    for p in pair_rows:
        er = p["fills_e"][entry_sc]
        xr = p["fills_x"][exit_sc]
        if er.get("fill_status") != "FILLED" or xr.get("fill_status") != "FILLED":
            continue
        et = datetime.fromisoformat(er["fill_event_time"])
        xt = datetime.fromisoformat(xr["fill_event_time"])
        if xt < et:
            continue
        pnl = trade_pnl(float(er["fill_price"]), float(xr["fill_price"]))
        trades.append(
            SimTrade(
                day=p["day"],
                symbol=p["symbol"],
                entry_time=et,
                exit_time=xt,
                entry_price=float(er["fill_price"]),
                exit_price=float(xr["fill_price"]),
                exit_reason=f"{entry_sc}_{exit_sc}",
                pnl_5bps=pnl,
                hold_sec=(xt - et).total_seconds(),
                entry_method="EC2",
                cohort="EC2_EG",
                setup_id=p["setup_id"],
                impulse_episode_id=p["episode_id"],
                breakout_episode_id=p["episode_id"],
                pbv2=False,
                vcie=True,
                mode=f"{entry_sc}_{exit_sc}",
                session=str(p.get("session") or "AM"),
            )
        )
    return trades


def run_historical_pairs(
    eval_pack: dict[str, Any],
    *,
    oos_days: Sequence[str],
    gate_ready: bool,
) -> dict[str, Any]:
    """Only formal if gate_ready; still compute for transparency with formal=false flag."""
    out = {}
    for e in ENTRY_LABELS:
        for x in EXIT_LABELS:
            # limit combinations to diagonal + E1/X1 family to avoid explosion: E0-E5 × X1 and E1 × X0-X5
            if not (x == "X1" or e == "E1"):
                continue
            trades = build_pair_trades(eval_pack["pair_rows"], entry_sc=e, exit_sc=x)
            s = _summarize_trades(trades, oos_days=oos_days, mode=f"{e}_{x}")
            s["formal"] = bool(gate_ready)
            s["evaluable_trades"] = len(trades)
            s["fill_coverage"] = round(len(trades) / max(1, eval_pack["n_strict"]), 4)
            out[f"{e}_{x}"] = s
    return out


def entry_scenario_summary(entry_rows: dict) -> dict[str, Any]:
    out = {}
    for e, rows in entry_rows.items():
        filled = [r for r in rows if r.get("fill_status") == "FILLED"]
        ne = [r for r in rows if r.get("fill_status") != "FILLED"]
        delays = [float(r["fill_delay_ms"]) for r in filled if r.get("fill_delay_ms") is not None]
        out[e] = {
            "n": len(rows),
            "filled": len(filled),
            "not_evaluable": len(ne),
            "coverage": round(len(filled) / max(1, len(rows)), 4),
            "mean_fill_delay_ms": round(sum(delays) / len(delays), 2) if delays else None,
            "sample": filled[:10] + ne[:5],
        }
    return out


def exit_scenario_summary(exit_rows: dict) -> dict[str, Any]:
    return entry_scenario_summary(exit_rows)
