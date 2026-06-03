#!/usr/bin/env python3
"""
Phase247 (review only): explain v2 evaluation gap between Phase244 and Phase246.

Questions to answer:
1) Are Phase244 v2>=5 (41 trades) and Phase246 v2_only (969 trades) same population?
2) How many of Phase244's 41 trades are included in Phase246 (by trade-id overlap)?
3) Breakdown of Phase246's 969 by stream: live / push_replay / replay, and by origin: accept vs counterfactual(max_concurrent)
4) Top sessions contributing to Phase244 PF=3.51 (v2>=5)
5) Top sessions generating loss in Phase246 v2_only
6) Split aggregation for accept-realized vs counterfactual-virtual in Phase246

Output:
kabu_native/results/reports/phase247_v2_gap_analysis.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
REPORTS = REPO / "kabu_native" / "results" / "reports"

PH244 = REPORTS / "phase244_fast_validation_coverage_expansion.json"
PH246 = REPORTS / "phase246_v2_priority_simulation.json"
OUT = REPORTS / "phase247_v2_gap_analysis.json"

MAX_POS = 3
TARGET_REASON = "max_concurrent"

V1_MODE = "legacy"
V1_RATIO = 0.85


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(r["pnl_pct"]) for r in rows]
    n = len(pnls)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
            "stop_rate": None,
            "avg_pnl_pct": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
    }


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    return []


def _read_summary(session_dir: Path) -> dict[str, Any]:
    p = session_dir / "small_paper_summary.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _classify_stream(session_id: str, summary: dict[str, Any]) -> str:
    sid = session_id.replace("\\", "/")
    base_name = sid.split("/")[-1].lower()
    mode = str((summary or {}).get("mode") or "").lower()
    source = str((summary or {}).get("source") or "").lower()

    if "push_replay" in base_name or "push_replay_sim" in sid.lower() or "push_replay" in mode or source in ("push-replay", "push_replay"):
        return "push_replay"
    if "live_session" in base_name or "live_full_session" in base_name or "live" in mode or source == "live":
        return "live"
    if source == "replay" or ("replay" in mode and "push" not in mode and "live" not in mode):
        return "replay"
    if "/" not in sid and len(sid) == 8 and sid.isdigit():
        return "replay"
    return "unknown"


def _extract_phase244_v2_ge5_trades(phase244: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Rebuild Phase244 v2>=5 population as trade rows with ids for overlap checks.
    Uses Phase243's _extract_closed_trades implementation (same as Phase244).
    """
    p243 = _load_module("phase243_for_phase247", "kabu_native/scripts/run_phase243_fast_validation_framework.py")

    trades: list[dict[str, Any]] = []
    for cov in phase244.get("coverage") or []:
        sid = str(cov.get("session_id") or "")
        if not sid:
            continue
        sdir = SMALL_PAPER / Path(sid)
        events = p243._load_events(sdir)  # type: ignore[attr-defined]
        rows = p243._extract_closed_trades(events)  # type: ignore[attr-defined]
        for r in rows:
            if not r.get("v2_ge5"):
                continue
            summ = _read_summary(sdir)
            trades.append(
                {
                    "session_id": sid,
                    "stream": _classify_stream(sid, summ),
                    "symbol": r["symbol"],
                    "entry_time": r["entry_time"],
                    "pnl_pct": float(r["pnl_pct"]),
                    "stop_hit": bool(r["stop_hit"]),
                    "exit_reason": str(r.get("exit_reason") or ""),
                    "origin": "accept_realized",
                }
            )
    return trades


@dataclass
class OpenTrade:
    symbol: str
    entry_time: str
    entry_price: float
    v2_ge5: bool
    origin: str  # accept_realized | counterfactual_max_concurrent


def _session_end(events: list[dict[str, Any]]) -> str:
    best = ""
    best_ts = 0.0
    for ev in events:
        t = str(ev.get("entry_time") or ev.get("event_time") or "")
        ts = _parse_ts(t)
        if ts >= best_ts:
            best_ts = ts
            best = t
    return best


def _v2_ge5(ev: dict[str, Any]) -> bool:
    flag = ev.get("entry_expectancy_score_v2_ge5_flag")
    if flag in (True, "True", "true", "1", 1):
        return True
    if flag in (False, "False", "false", "0", 0):
        return False
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    fields = compute_entry_expectancy_score_fields(trade=ev)
    return int(fields.get("entry_expectancy_score_v2") or 0) >= 5


def _simulate_phase246_v2_only_trades(p71: Any, session_id: str, session_dir: Path) -> list[dict[str, Any]]:
    """
    Re-simulate Phase246 scenario C (v2-only) but return trade-level rows with ids + origin.
    """
    events = _load_events(session_dir)
    if not events:
        return []
    # stable time ordering
    def _k(ev: dict[str, Any]) -> tuple[float, int]:
        return (_parse_ts(str(ev.get("event_time") or ev.get("entry_time") or "")), int(ev.get("message_index") or 0))

    events = sorted(events, key=_k)
    end = _session_end(events)

    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}  # symbol -> ActiveTrade
    open_meta: dict[str, OpenTrade] = {}  # symbol -> OpenTrade
    completed: list[dict[str, Any]] = []

    pending_time: Optional[str] = None
    pending: list[dict[str, Any]] = []

    def close(sym: str, close_time: str, close_price: float, reason: str) -> None:
        act = active.pop(sym, None)
        meta = open_meta.pop(sym, None)
        if not act or not meta:
            return
        pnl = float(p71._pnl_pct(act.trade.entry_price, close_price))
        stop = str(reason) == "stop_hit" or _boolish(getattr(act.trade, "stop_hit", False))
        completed.append(
            {
                "session_id": session_id,
                "symbol": meta.symbol,
                "entry_time": meta.entry_time,
                "pnl_pct": pnl,
                "stop_hit": stop,
                "close_reason": str(reason),
                "origin": meta.origin,
                "v2_ge5": meta.v2_ge5,
            }
        )

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        # scenario C: keep only v2_ge5 decisions, preserve message_index order
        keep = [ev for ev in pending if _v2_ge5(ev)]
        keep.sort(key=lambda ev: int(ev.get("message_index") or 0))
        for ev in keep:
            sym = str(ev.get("symbol") or "")
            ent = str(ev.get("entry_time") or ev.get("event_time") or "")
            px = _float(ev.get("current_price")) or _float(ev.get("entry_price")) or 0.0
            if not sym or not ent or px <= 0:
                continue
            if sym in active:
                continue
            if len(active) >= MAX_POS:
                # cannot open due to cap; ignore for scenario C trade list
                continue
            ts = p71._parse_ts(ent) if hasattr(p71, "_parse_ts") else _parse_ts(ent)
            st = sym_states.setdefault(sym, p71.SymState())
            comps = p71._components(st, ts=ts, price=float(px), ev=ev)
            q = _float(ev.get("continuation_quality_score")) or 0.0

            tr = p71.StructuralTrade(sym, ent, float(px), float(q))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": float(px),
                        "pnl_pct": 0.0,
                        "quality": comps["quality"],
                        "momentum": comps["momentum"],
                        "favorable": comps["favorable"],
                        "pure_price_momentum": comps["pure_price_momentum"],
                        "vwap_strength": comps["vwap_strength"],
                        "mfe_proxy": comps["mfe_proxy"],
                    }
                ],
            )
            origin = "accept_realized" if str(ev.get("event_type") or "") == "accepted" else "counterfactual_max_concurrent"
            open_meta[sym] = OpenTrade(symbol=sym, entry_time=ent, entry_price=float(px), v2_ge5=True, origin=origin)
        pending = []

    def eligible_decision(ev: dict[str, Any]) -> bool:
        et = str(ev.get("event_type") or "")
        if et not in ("accepted", "rejected"):
            return False
        reason = str(ev.get("gate_reject_reason") or "")
        return et == "accepted" or reason == TARGET_REASON

    for ev in events:
        ev_time = str(ev.get("event_time") or "")
        if pending_time is None:
            pending_time = ev_time
        if ev_time != pending_time:
            flush_pending()
            pending_time = ev_time

        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = _float(ev.get("current_price")) or 0.0

        if et == "candidate" and sym in active and px > 0 and ent:
            ts = p71._parse_ts(ent) if hasattr(p71, "_parse_ts") else _parse_ts(ent)
            st = sym_states.setdefault(sym, p71.SymState())
            act = active[sym]
            comps = p71._components(st, ts=ts, price=float(px), ev=ev)
            act.rich_ticks.append(
                {
                    "price": float(px),
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, float(px)),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                close(sym, ent, float(px), str(reason))

        if eligible_decision(ev):
            pending.append(ev)

    flush_pending()

    # close remaining at end
    for sym in list(active.keys()):
        act = active[sym]
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close(sym, end, float(last_px), "session_end")

    return completed


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    phase244 = _read_json(PH244)
    phase246 = _read_json(PH246)

    p71 = _load_module("phase71_engine_p247", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    # Phase244 v2>=5 trade list (41 expected)
    ph244_trades = _extract_phase244_v2_ge5_trades(phase244)
    ph244_ids = {(t["session_id"], t["symbol"], t["entry_time"]) for t in ph244_trades}

    # Phase246 v2_only trade list (969 expected) by resim, with stream + origin breakdown
    ph246_v2_only_trades: list[dict[str, Any]] = []
    by_stream_counts: dict[str, int] = {}
    by_origin_counts: dict[str, int] = {}

    # Use the same session set as Phase246 (by_session list)
    for sess in phase246.get("by_session") or []:
        sid = str(sess.get("session_id") or "")
        if not sid:
            continue
        sdir = SMALL_PAPER / Path(sid)
        summ = _read_summary(sdir)
        stream = _classify_stream(sid, summ)
        rows = _simulate_phase246_v2_only_trades(p71, sid, sdir)
        for r in rows:
            r["stream"] = stream
        ph246_v2_only_trades.extend(rows)
        by_stream_counts[stream] = by_stream_counts.get(stream, 0) + len(rows)
        for r in rows:
            o = str(r["origin"])
            by_origin_counts[o] = by_origin_counts.get(o, 0) + 1

    ph246_ids = {(t["session_id"], t["symbol"], t["entry_time"]) for t in ph246_v2_only_trades}
    overlap = sorted(list(ph244_ids & ph246_ids))

    # Phase246 v2_only split metrics
    ph246_accept = [t for t in ph246_v2_only_trades if t["origin"] == "accept_realized"]
    ph246_cf = [t for t in ph246_v2_only_trades if t["origin"] == "counterfactual_max_concurrent"]

    # Top sessions: Phase244 contributors (by total pnl, v2>=5)
    ph244_by_session: dict[str, list[dict[str, Any]]] = {}
    for t in ph244_trades:
        ph244_by_session.setdefault(t["session_id"], []).append(t)
    ph244_session_rows = []
    for sid, rows in ph244_by_session.items():
        m = _metrics(rows)
        ph244_session_rows.append(
            {
                "session_id": sid,
                "stream": rows[0].get("stream"),
                "trade_count": m["trade_count"],
                "profit_factor": m["profit_factor"],
                "total_pnl_pct": m["total_pnl_pct"],
                "win_rate": m["win_rate"],
            }
        )
    ph244_top_sessions = sorted(ph244_session_rows, key=lambda r: float(r["total_pnl_pct"]), reverse=True)[:10]

    # Top loss sessions: Phase246 v2_only (by total pnl)
    ph246_by_session: dict[str, list[dict[str, Any]]] = {}
    for t in ph246_v2_only_trades:
        ph246_by_session.setdefault(t["session_id"], []).append(t)
    ph246_session_rows = []
    for sid, rows in ph246_by_session.items():
        m = _metrics(rows)
        ph246_session_rows.append(
            {
                "session_id": sid,
                "stream": rows[0].get("stream"),
                "trade_count": m["trade_count"],
                "profit_factor": m["profit_factor"],
                "total_pnl_pct": m["total_pnl_pct"],
                "avg_pnl_pct": m["avg_pnl_pct"],
            }
        )
    ph246_top_loss_sessions = sorted(ph246_session_rows, key=lambda r: float(r["total_pnl_pct"]))[:10]

    # Answer Q1 explicitly: session sets
    ph244_sessions = sorted({str(c.get("session_id") or "") for c in phase244.get("coverage") or [] if c.get("session_id")})
    ph246_sessions = sorted({str(s.get("session_id") or "") for s in phase246.get("by_session") or [] if s.get("session_id")})
    ph244_session_set = set(ph244_sessions)
    ph246_session_set = set(ph246_sessions)

    report = {
        "phase": 247,
        "mode": "v2_gap_analysis",
        "constraints": {
            "review_only": True,
            "entry_change_forbidden": True,
            "score_change_forbidden": True,
            "yaml_change_forbidden": True,
            "production_change_forbidden": True,
        },
        "inputs": {
            "phase244_report": str(PH244),
            "phase246_report": str(PH246),
        },
        "q1_population_same": {
            "same_population": False,
            "phase244_sessions_scanned": int(phase244.get("population", {}).get("sessions_scanned") or 0),
            "phase246_sessions_scanned": int(phase246.get("population", {}).get("sessions_scanned") or 0),
            "phase244_session_ids": ph244_sessions,
            "phase246_session_ids_count": len(ph246_sessions),
            "phase244_is_subset_of_phase246": ph244_session_set.issubset(ph246_session_set),
            "phase246_extra_sessions_count": len(ph246_session_set - ph244_session_set),
            "notes": [
                "Phase244 targets replay/push_replay history subset (13 sessions).",
                "Phase246 uses Phase245 population (38 sessions) and is a cap-3 order simulation including live sessions and max_concurrent counterfactual admissions.",
            ],
        },
        "q2_overlap_trade_ids": {
            "phase244_v2_ge5_trade_count": len(ph244_trades),
            "phase246_v2_only_trade_count": len(ph246_v2_only_trades),
            "overlap_trade_count": len(overlap),
            "overlap_sample_head_20": [{"session_id": a, "symbol": b, "entry_time": c} for (a, b, c) in overlap[:20]],
        },
        "q3_phase246_969_breakdown": {
            "by_stream_trade_count": by_stream_counts,
            "by_origin_trade_count": by_origin_counts,
        },
        "q4_phase244_pf351_top_sessions": ph244_top_sessions,
        "q5_phase246_v2_only_top_loss_sessions": ph246_top_loss_sessions,
        "q6_phase246_split_accept_vs_counterfactual": {
            "accept_realized": {"trade_count": len(ph246_accept), "metrics": _metrics(ph246_accept)},
            "counterfactual_max_concurrent": {"trade_count": len(ph246_cf), "metrics": _metrics(ph246_cf)},
        },
        "explanation_hypotheses": [
            "Phase244 v2>=5 uses realized PnL from ACCEPTED trades only (no max_concurrent counterfactual), and only on a 13-session replay/push_replay subset.",
            "Phase246 v2_only is a cap-3 admission simulation that (a) includes live sessions and (b) admits counterfactual(max_concurrent) candidates, expanding the population and changing which trades get taken under the same cap.",
            "Therefore PF can drop materially because the population and the decision competition (within cap=3) differ, even if v2>=5 is strong on the realized-accept subset.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ph244_v2_ge5={len(ph244_trades)} ph246_v2_only={len(ph246_v2_only_trades)} overlap={len(overlap)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

