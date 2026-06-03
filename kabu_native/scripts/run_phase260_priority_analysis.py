#!/usr/bin/env python3
"""
Phase260: max_concurrent priority factor analysis (review only).

Compare accepted vs max_concurrent-rejected candidates on:
  - quality (continuation_quality_score)
  - entry_score (entry_expectancy_score v1)
  - entry_score_v2
  - imbalance (entry_order_book_imbalance)

Uses virtual PnL replay for max_concurrent (Phase245 method) and
closed-trade PnL for accepted (Phase243 method).

Output: kabu_native/results/reports/phase260_priority_analysis.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase260_priority_analysis.json"

TARGET_REASON = "max_concurrent"
V1_MODE = "legacy"
V1_RATIO = 0.85

FACTORS = (
    ("quality", "continuation_quality_score"),
    ("entry_score", "entry_expectancy_score"),
    ("entry_score_v2", "entry_expectancy_score_v2"),
    ("imbalance", "entry_order_book_imbalance"),
)


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


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    return str(val).lower() in ("true", "1", "yes")


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


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


def _discover_sessions(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for summary_path in sorted(base.rglob("small_paper_summary.json")):
        sdir = summary_path.parent
        summ = _read_summary(sdir)
        out.append(
            {
                "session_id": sdir.relative_to(base).as_posix(),
                "session_dir": str(sdir),
                "mode": summ.get("mode"),
                "source": summ.get("source"),
            }
        )
    return out


def _enrich_factors(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    q = _float(ev.get("continuation_quality_score"))
    imb = _float(ev.get("entry_order_book_imbalance"))
    v1 = _int(ev.get("entry_expectancy_score"))
    v2 = _int(ev.get("entry_expectancy_score_v2"))
    if v1 is None or v2 is None:
        sf = compute_entry_expectancy_score_fields(trade=ev)
        v1 = _int(sf.get("entry_expectancy_score")) if v1 is None else v1
        v2 = _int(sf.get("entry_expectancy_score_v2")) if v2 is None else v2
    return {
        "quality": q,
        "entry_score": float(v1) if v1 is not None else None,
        "entry_score_v2": float(v2) if v2 is not None else None,
        "imbalance": imb,
    }


@dataclass
class TradeObs:
    cohort: str
    symbol: str
    entry_time: str
    pnl_pct: float
    stop_hit: bool
    quality: Optional[float]
    entry_score: Optional[float]
    entry_score_v2: Optional[float]
    imbalance: Optional[float]
    session_id: str


def _metrics(rows: list[TradeObs]) -> dict[str, Any]:
    pnls = [r.pnl_pct for r in rows]
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
    stops = sum(1 for r in rows if r.stop_hit)
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
    }


def _extract_accepted(session_id: str, events: list[dict[str, Any]]) -> list[TradeObs]:
    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("event_type") or "") != "accepted":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key[0] and key[1]:
            accepts[key] = ev

    exits: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("event_type") or "") != "observer_exit":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key[0] and key[1]:
            exits[key] = ev

    out: list[TradeObs] = []
    for key, acc in accepts.items():
        ex = exits.get(key)
        if ex:
            pnl = _float(ex.get("pnl_pct"))
            reason = str(ex.get("exit_reason") or "")
            stop = _boolish(ex.get("stop_hit")) or reason == "stop_hit"
        else:
            pnl = _float(acc.get("pnl_pct"))
            reason = str(acc.get("exit_reason") or "")
            stop = _boolish(acc.get("stop_hit")) or reason == "stop_hit"
        if pnl is None:
            continue
        fac = _enrich_factors(acc)
        out.append(
            TradeObs(
                cohort="accepted",
                symbol=key[0],
                entry_time=key[1],
                pnl_pct=float(pnl),
                stop_hit=stop,
                quality=fac["quality"],
                entry_score=fac["entry_score"],
                entry_score_v2=fac["entry_score_v2"],
                imbalance=fac["imbalance"],
                session_id=session_id,
            )
        )
    return out


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


def _first_max_concurrent(events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("gate_reject_reason") or "") != TARGET_REASON:
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        if not sym or not ent:
            continue
        key = (sym, ent)
        if key in chosen:
            continue
        px = _float(ev.get("current_price"))
        if px is None or px <= 0:
            continue
        chosen[key] = ev
    return chosen


def _replay_max_concurrent(
    p71: Any, session_id: str, events: list[dict[str, Any]]
) -> list[TradeObs]:
    session_end = _session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[TradeObs] = []
    inject = _first_max_concurrent(events)
    injected: set[tuple[str, str]] = set()

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        tr = act.trade
        pnl = p71._pnl_pct(tr.entry_price, close_price)
        stop = str(reason) == "stop_hit"
        fac = getattr(tr, "entry_factors", {}) or {}
        completed.append(
            TradeObs(
                cohort="max_concurrent",
                symbol=tr.symbol,
                entry_time=tr.entry_time,
                pnl_pct=float(pnl),
                stop_hit=stop,
                quality=fac.get("quality"),
                entry_score=fac.get("entry_score"),
                entry_score_v2=fac.get("entry_score_v2"),
                imbalance=fac.get("imbalance"),
                session_id=session_id,
            )
        )

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        ent = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent) if hasattr(p71, "_parse_ts") else _parse_ts(ent)
        price = _float(ev.get("current_price")) or 0.0
        if price <= 0:
            continue
        st = sym_states.setdefault(sym, p71.SymState())
        key = (sym, ent)
        if key in inject and key not in injected:
            injected.add(key)
            if sym in active:
                old = active.pop(sym)
                close_act(old, close_time=ent, close_price=float(price), reason="overlap_replaced_review")
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            src = inject[key]
            fac = _enrich_factors(src)
            q = fac["quality"] or _float(ev.get("continuation_quality_score")) or 0.0
            tr = p71.StructuralTrade(sym, ent, float(price), float(q))
            setattr(tr, "entry_factors", fac)
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": float(price),
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
        et = str(ev.get("event_type") or "")
        if et == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            act.rich_ticks.append(
                {
                    "price": float(price),
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, float(price)),
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
                close_act(act, close_time=ent, close_price=float(price), reason=str(reason))
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")
    return completed


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    try:
        return round(statistics.correlation(xs, ys), 4)
    except statistics.StatisticsError:
        return None


def _quartile_edges(vals: list[float]) -> list[float]:
    if len(vals) < 4:
        return []
    s = sorted(vals)
    n = len(s)

    def q(p: float) -> float:
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        w = idx - lo
        return s[lo] * (1 - w) + s[hi] * w

    return [q(0.25), q(0.5), q(0.75)]


def _quartile_label(v: float, edges: list[float]) -> str:
    if not edges:
        return "all"
    if v <= edges[0]:
        return "Q1_low"
    if v <= edges[1]:
        return "Q2"
    if v <= edges[2]:
        return "Q3"
    return "Q4_high"


def _factor_analysis(rows: list[TradeObs], factor: str) -> dict[str, Any]:
    getter: Callable[[TradeObs], Optional[float]]
    if factor == "quality":
        getter = lambda r: r.quality
    elif factor == "entry_score":
        getter = lambda r: r.entry_score
    elif factor == "entry_score_v2":
        getter = lambda r: r.entry_score_v2
    else:
        getter = lambda r: r.imbalance

    paired = [(getter(r), r.pnl_pct) for r in rows if getter(r) is not None]
    if not paired:
        return {"coverage_count": 0, "coverage_pct": 0.0}

    xs = [float(p[0]) for p in paired]
    ys = [float(p[1]) for p in paired]
    edges = _quartile_edges(xs)
    by_q: dict[str, list[float]] = {}
    for x, y in zip(xs, ys):
        lbl = _quartile_label(x, edges)
        by_q.setdefault(lbl, []).append(y)

    q_metrics = {k: _metrics_from_pnls(v) for k, v in sorted(by_q.items())}
    top = by_q.get("Q4_high", [])
    bot = by_q.get("Q1_low", [])
    top_avg = round(sum(top) / len(top), 6) if top else None
    bot_avg = round(sum(bot) / len(bot), 6) if bot else None
    spread = round(top_avg - bot_avg, 6) if top_avg is not None and bot_avg is not None else None

    winners = [y for _, y in paired if y > 0]
    top_set = set(top)
    winner_in_top_q4 = None
    if winners:
        winner_in_top_q4 = round(
            sum(1 for x, y in zip(xs, ys) if y > 0 and y in top_set) / len(winners), 4
        )

    return {
        "coverage_count": len(paired),
        "coverage_pct": round(100.0 * len(paired) / len(rows), 2) if rows else 0.0,
        "mean_factor": round(sum(xs) / len(xs), 6),
        "pearson_vs_pnl": _pearson(xs, ys),
        "quartile_metrics": q_metrics,
        "top_quartile_avg_pnl_pct": top_avg,
        "bottom_quartile_avg_pnl_pct": bot_avg,
        "top_minus_bottom_quartile_spread": spread,
        "winner_capture_top_quartile_rate": winner_in_top_q4,
    }


def _metrics_from_pnls(pnls: list[float]) -> dict[str, Any]:
    n = len(pnls)
    if n == 0:
        return {"trade_count": 0, "avg_pnl_pct": None, "profit_factor": None, "win_rate": None}
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "profit_factor": pf if pf != float("inf") else pf,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
    }


def _cohort_factor_means(rows: list[TradeObs]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, _ in FACTORS:
        vals = []
        for r in rows:
            v = getattr(r, name if name != "entry_score" else "entry_score", None)
            if name == "quality":
                v = r.quality
            elif name == "entry_score":
                v = r.entry_score
            elif name == "entry_score_v2":
                v = r.entry_score_v2
            else:
                v = r.imbalance
            if v is not None:
                vals.append(float(v))
        out[name] = {
            "count": len(vals),
            "mean": round(sum(vals) / len(vals), 6) if vals else None,
        }
    return out


def _rank_factors(mc_rows: list[TradeObs]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for name, _ in FACTORS:
        fa = _factor_analysis(mc_rows, name)
        corr = fa.get("pearson_vs_pnl")
        spread = fa.get("top_minus_bottom_quartile_spread")
        scored.append(
            {
                "factor": name,
                "pearson_vs_pnl": corr,
                "top_minus_bottom_quartile_spread": spread,
                "top_quartile_avg_pnl_pct": fa.get("top_quartile_avg_pnl_pct"),
                "coverage_count": fa.get("coverage_count"),
            }
        )

    def sort_key(item: dict[str, Any]) -> tuple[float, float]:
        c = item.get("pearson_vs_pnl")
        s = item.get("top_minus_bottom_quartile_spread")
        return (
            float(c) if c is not None else -999.0,
            float(s) if s is not None else -999.0,
        )

    ranked = sorted(scored, key=sort_key, reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["priority_rank"] = i
    return ranked


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    p71 = _load_module("phase71_engine_p260", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")
    sessions = _discover_sessions(SMALL_PAPER)

    accepted_all: list[TradeObs] = []
    mc_all: list[TradeObs] = []
    per_session: list[dict[str, Any]] = []

    for i, sess in enumerate(sessions, 1):
        sid = str(sess["session_id"])
        sdir = Path(sess["session_dir"])
        events = _load_events(sdir)
        if not events:
            continue
        acc = _extract_accepted(sid, events)
        mc = _replay_max_concurrent(p71, sid, events)
        accepted_all.extend(acc)
        mc_all.extend(mc)
        per_session.append(
            {
                "session_id": sid,
                "accepted_count": len(acc),
                "max_concurrent_count": len(mc),
            }
        )
        if i % 25 == 0:
            print(f"  [{i}/{len(sessions)}] scanned...", flush=True)

    factor_names = [f[0] for f in FACTORS]
    accepted_by_factor = {f: _factor_analysis(accepted_all, f) for f in factor_names}
    mc_by_factor = {f: _factor_analysis(mc_all, f) for f in factor_names}
    priority_ranking = _rank_factors(mc_all)

    best = priority_ranking[0] if priority_ranking else {}
    report = {
        "phase": 260,
        "mode": "max_concurrent_priority_factor_analysis",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "entry_changed": False,
            "universe_changed": False,
            "exit_changed": False,
        },
        "population": {
            "sessions_scanned": len(sessions),
            "sessions_with_data": len(per_session),
            "accepted_trade_count": len(accepted_all),
            "max_concurrent_virtual_trade_count": len(mc_all),
            "max_concurrent_method": "Phase245 virtual replay (first mc reject per symbol+entry_time)",
            "accepted_method": "accept+observer_exit pnl (Phase243)",
        },
        "cohort_summary": {
            "accepted": _metrics(accepted_all),
            "max_concurrent": _metrics(mc_all),
        },
        "cohort_factor_means": {
            "accepted": _cohort_factor_means(accepted_all),
            "max_concurrent": _cohort_factor_means(mc_all),
        },
        "factor_analysis": {
            "accepted": accepted_by_factor,
            "max_concurrent": mc_by_factor,
        },
        "priority_ranking_max_concurrent": priority_ranking,
        "recommendation": {
            "best_priority_factor": best.get("factor"),
            "rationale": (
                "Ranked by Pearson correlation with virtual PnL among max_concurrent cohort, "
                "then top-minus-bottom quartile spread. Higher = better signal for cap-saturation priority."
            ),
            "pearson_vs_pnl": best.get("pearson_vs_pnl"),
            "top_minus_bottom_quartile_spread": best.get("top_minus_bottom_quartile_spread"),
            "note": (
                "Imbalance coverage may be lower on older sessions missing entry_order_book_imbalance. "
                "Scores recomputed offline when not logged on reject rows."
            ),
        },
        "per_session_counts_sample": per_session[:20],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(
        f"accepted={len(accepted_all)} mc={len(mc_all)} best_factor={best.get('factor')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
