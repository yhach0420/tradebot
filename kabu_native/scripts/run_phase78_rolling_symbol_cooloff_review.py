#!/usr/bin/env python3
"""
Phase 78: Rolling OOS symbol cooloff design & backtest (read-only).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
OUTPUT_SESSION = SMALL_PAPER / "20260520" / "push_replay_231314"

SESSION_PATHS = [
    "20260518/push_replay_220451",
    "20260519/live_full_session_081047",
    "20260520/push_replay_001932",
    "20260520/live_full_session_080745",
    "20260520/push_replay_231314",
]

RULES = (
    ("A_no_filter", "no_filter"),
    ("B_prior_total_pnl_lt_minus_0.2", "prior_total_pnl"),
    ("C_prior_pf_lt_0.8_trades_ge_5", "prior_pf"),
    ("D_prior_avg_pnl_lt_0_trades_ge_5", "prior_avg_pnl"),
    ("E_prior_loss_streak_ge_2", "loss_streak"),
    ("F_prior_session_worst_1", "prior_worst_1"),
)

V1_MODE = "legacy"
V1_RATIO = 0.85


def _load_phase71():
    path = Path(__file__).resolve().parent / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p78"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def _load_trades_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [
            {
                "symbol": r["symbol"],
                "entry_time": r["entry_time"],
                "entry_price": float(r["entry_price"] or 0),
                "close_reason": r.get("close_reason", ""),
                "realized_pnl_pct": float(r["realized_pnl_pct"] or 0),
                "hold_duration_sec": float(r.get("hold_duration_sec") or 0),
            }
            for r in csv.DictReader(f)
        ]


def _symbol_session_stats(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[float]] = defaultdict(list)
    reasons: dict[str, Counter] = defaultdict(Counter)
    holds: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        sym = str(t["symbol"])
        pnl = float(t["realized_pnl_pct"])
        by_sym[sym].append(pnl)
        reasons[sym][str(t.get("close_reason") or "")] += 1
        holds[sym].append(float(t.get("hold_duration_sec") or 0))
    rows: list[dict[str, Any]] = []
    for sym, pnls in sorted(by_sym.items()):
        pf = _profit_factor(pnls)
        rows.append(
            {
                "symbol": sym,
                "trades": len(pnls),
                "total_pnl_pct": round(sum(pnls), 4),
                "avg_pnl_pct": round(statistics.mean(pnls), 4),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
                "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "max_loss_pct": round(min(pnls), 4),
                "exit_reason_distribution": json.dumps(dict(reasons[sym]), ensure_ascii=False),
                "avg_hold_sec": round(statistics.mean(holds[sym]), 1),
            }
        )
    rows.sort(key=lambda r: r["total_pnl_pct"])
    return rows


@dataclass
class SymbolPriorState:
    total_pnl: float = 0.0
    trade_count: int = 0
    pnls: list[float] = field(default_factory=list)
    session_totals: list[float] = field(default_factory=list)


def _update_prior(state: dict[str, SymbolPriorState], trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    stats = _symbol_session_stats(trades)
    for row in stats:
        sym = str(row["symbol"])
        st = state.setdefault(sym, SymbolPriorState())
        tp = float(row["total_pnl_pct"])
        st.total_pnl += tp
        st.trade_count += int(row["trades"])
        st.session_totals.append(tp)
    for t in trades:
        state[str(t["symbol"])].pnls.append(float(t["realized_pnl_pct"]))
    return stats


def _compute_exclusions(
    rule_key: str,
    prior: dict[str, SymbolPriorState],
    *,
    last_session_stats: Sequence[Mapping[str, Any]],
) -> set[str]:
    if rule_key == "no_filter":
        return set()
    if rule_key == "prior_worst_1":
        if not last_session_stats:
            return set()
        worst = min(last_session_stats, key=lambda r: float(r["total_pnl_pct"]))
        return {str(worst["symbol"])}

    excluded: set[str] = set()
    for sym, st in prior.items():
        if rule_key == "prior_total_pnl" and st.total_pnl < -0.2:
            excluded.add(sym)
        elif rule_key == "prior_pf" and st.trade_count >= 5:
            pf = _profit_factor(st.pnls)
            if pf is not None and pf != float("inf") and pf < 0.8:
                excluded.add(sym)
        elif rule_key == "prior_avg_pnl" and st.trade_count >= 5:
            if st.total_pnl / st.trade_count < 0:
                excluded.add(sym)
        elif rule_key == "loss_streak":
            streak = 0
            for tp in reversed(st.session_totals):
                if tp < 0:
                    streak += 1
                else:
                    break
            if streak >= 2:
                excluded.add(sym)
    return excluded


def _summarize_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "structural_pf": None,
            "avg_pnl": None,
            "win_rate": None,
            "max_loss": None,
            "trade_count": 0,
        }
    pnls = [float(t["realized_pnl_pct"]) for t in trades]
    pf = _profit_factor(pnls)
    return {
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl": round(statistics.mean(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
        "max_loss": round(min(pnls), 4),
        "trade_count": len(pnls),
    }


def _replay_v1_trades(p71: Any, session_dir: Path) -> list[dict[str, Any]]:
    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        return []
    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[Any] = []

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = p71._pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, p71._parse_ts(close_time) - act.entry_ts), 1)
        completed.append(act.trade)

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent_raw)
        price = float(ev.get("current_price") or 0)
        if price <= 0:
            continue
        st = sym_states.setdefault(sym, p71.SymState())

        if et == "accepted":
            if sym in active:
                old = active.pop(sym)
                close_act(old, close_time=ent_raw, close_price=price, reason="overlap_replaced_review")
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(sym, ent_raw, price, float(ev.get("continuation_quality_score") or 0))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": price,
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
        elif et == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            act.rich_ticks.append(
                {
                    "price": price,
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, price),
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
                close_act(act, close_time=ent_raw, close_price=price, reason=reason)
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "realized_pnl_pct": t.realized_pnl_pct,
            "hold_duration_sec": t.hold_duration_sec,
            "close_reason": t.close_reason,
        }
        for t in completed
    ]


def _evaluate_session_oos(
    session_id: str,
    trades: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    excluded: set[str],
    *,
    rule_id: str,
    prior_session_count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trade_by_entry = {(t["symbol"], t["entry_time"]): t for t in trades}
    gate_accepts = 0
    rejected = 0
    missed_winners = 0
    avoided_losers = 0
    cases: list[dict[str, Any]] = []
    blocked_entries: set[tuple[str, str]] = set()

    for ev in events:
        if str(ev.get("event_type")) != "accepted":
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        gate_accepts += 1
        if sym not in excluded:
            continue
        rejected += 1
        blocked_entries.add((sym, ent))
        tr = trade_by_entry.get((sym, ent))
        pnl = float(tr["realized_pnl_pct"]) if tr else None
        if pnl is not None and pnl > 0:
            missed_winners += 1
            outcome = "missed_winner"
        elif pnl is not None:
            avoided_losers += 1
            outcome = "avoided_loser"
        else:
            outcome = "no_matching_trade"
        cases.append(
            {
                "session_id": session_id,
                "rule_id": rule_id,
                "symbol": sym,
                "entry_time": ent,
                "realized_pnl_pct_if_traded": pnl,
                "outcome": outcome,
            }
        )

    kept = [t for t in trades if (t["symbol"], t["entry_time"]) not in blocked_entries]
    metrics = _summarize_trades(kept)
    kept_pnls = [float(t["realized_pnl_pct"]) for t in kept]
    row = {
        "session_id": session_id,
        "rule_id": rule_id,
        "prior_session_count": prior_session_count,
        "excluded_symbols": "|".join(sorted(excluded)),
        "excluded_symbol_count": len(excluded),
        "gate_accept_events": gate_accepts,
        "accepted_count": gate_accepts - rejected,
        "rejected_by_cooloff": rejected,
        "missed_winners": missed_winners,
        "avoided_losers": avoided_losers,
        "total_pnl_pct": round(sum(kept_pnls), 4) if kept_pnls else 0.0,
        **metrics,
        "_kept_pnls": kept_pnls,
    }
    return row, cases


def _aggregate_oos(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    all_pnls: list[float] = []
    for r in rows:
        all_pnls.extend(r.get("_kept_pnls") or [])
    pf = _profit_factor(all_pnls) if all_pnls else None
    return {
        "oos_session_count": len(rows),
        "aggregate_structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "aggregate_trade_count": sum(int(r.get("trade_count") or 0) for r in rows),
        "aggregate_rejected_by_cooloff": sum(int(r.get("rejected_by_cooloff") or 0) for r in rows),
        "aggregate_missed_winners": sum(int(r.get("missed_winners") or 0) for r in rows),
        "aggregate_avoided_losers": sum(int(r.get("avoided_losers") or 0) for r in rows),
        "mean_session_pf": round(
            statistics.mean(float(r["structural_pf"]) for r in rows if r.get("structural_pf")),
            4,
        )
        if any(r.get("structural_pf") for r in rows)
        else None,
    }


def _build_5803_analysis(
    perf_rows: Sequence[Mapping[str, Any]],
    exclusion_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sym = "5803.T"
    prior_pnl = sum(
        float(r["total_pnl_pct"])
        for r in perf_rows
        if r["symbol"] == sym and r["session_id"] != "20260520/push_replay_231314"
    )
    excl_231314 = {
        str(r["rule_id"]): r.get("excluded_symbols", "")
        for r in exclusion_rows
        if r["session_id"] == "20260520/push_replay_231314"
    }
    return {
        "symbol": sym,
        "prior_cumulative_pnl_pct_before_231314": round(prior_pnl, 4),
        "rule_B_excludes_on_231314": sym in (excl_231314.get("B_prior_total_pnl_lt_minus_0.2") or ""),
        "rule_F_excludes_on_231314": sym in (excl_231314.get("F_prior_session_worst_1") or ""),
        "detected_before_20260520_sessions": sym
        in (excl_231314.get("F_prior_session_worst_1") or ""),
        "note": (
            "5803 was profitable in 20260519 and 001932; cumulative prior stays positive "
            "so B/C/D do not block on first 231314 day. F blocks after bad prior session (080745)."
        ),
    }


def _recommend(
    grid: Sequence[Mapping[str, Any]],
    *,
    session_count: int,
    detect_5803: bool,
) -> tuple[str, str]:
    oos_n = int(grid[0].get("oos_session_count") or 0) if grid else 0
    if session_count < 4 or oos_n < 2:
        return (
            "collect_more_sessions",
            f"{session_count} sessions, {oos_n} OOS-evaluable; need more history for rolling cooloff",
        )
    a = next(r for r in grid if r["rule_id"] == "A_no_filter")
    pf_a = float(a.get("aggregate_structural_pf") or 0)
    candidates = [r for r in grid if r["rule_id"] != "A_no_filter"]
    best = max(candidates, key=lambda r: float(r.get("aggregate_structural_pf") or 0), default=a)
    pf_b = float(best.get("aggregate_structural_pf") or 0)
    avoided = int(best.get("aggregate_avoided_losers") or 0)
    missed = int(best.get("aggregate_missed_winners") or 0)
    if pf_b > pf_a + 0.03 and avoided >= missed:
        return (
            "add_rolling_symbol_cooloff",
            f"{best['rule_id']}: OOS aggregate PF {pf_b} vs A {pf_a}; "
            f"avoided_losers={avoided} missed_winners={missed}; 5803_OOS_detected={detect_5803}",
        )
    if detect_5803:
        return (
            "inconclusive",
            f"5803.T flagged OOS before 20260520 sessions but PF gain limited "
            f"(best {best['rule_id']} PF={pf_b} vs A={pf_a})",
        )
    return "keep_current_universe", f"best OOS PF {pf_b} vs A {pf_a}"


def main() -> int:
    p71 = _load_phase71()
    sessions: list[dict[str, Any]] = []

    for rel in SESSION_PATHS:
        sdir = SMALL_PAPER / rel
        if not sdir.is_dir():
            continue
        # Always replay v1 from events (CSV may reflect v2 price_mom observer runs).
        trades = _replay_v1_trades(p71, sdir)
        source = "replayed_v1_from_events"
        if not trades and (sdir / "structural_trades.csv").is_file():
            trades = _load_trades_csv(sdir / "structural_trades.csv")
            source = "structural_trades.csv_fallback"
        events = []
        if (sdir / "small_paper_events.jsonl").is_file():
            events = p71._load_events(sdir / "small_paper_events.jsonl")
        sessions.append(
            {
                "session_id": rel,
                "trades": trades,
                "events": events,
                "trades_source": source,
                "symbol_stats": _symbol_session_stats(trades),
            }
        )

    prior_state: dict[str, SymbolPriorState] = {}
    last_session_stats: list[dict[str, Any]] = []

    perf_rows: list[dict[str, Any]] = []
    oos_detail_rows: list[dict[str, Any]] = []
    exclusion_rows: list[dict[str, Any]] = []
    all_cases: list[dict[str, Any]] = []
    per_rule_oos_lists: dict[str, list[dict[str, Any]]] = {rid: [] for rid, _ in RULES}
    detect_5803 = False

    for si, s in enumerate(sessions):
        prior_n = si
        for row in s["symbol_stats"]:
            perf_rows.append({"session_id": s["session_id"], **row})

        for rule_id, rule_key in RULES:
            excluded = _compute_exclusions(
                rule_key, prior_state, last_session_stats=last_session_stats
            )
            ev, cases = _evaluate_session_oos(
                s["session_id"],
                s["trades"],
                s["events"],
                excluded,
                rule_id=rule_id,
                prior_session_count=prior_n,
            )
            oos_detail_rows.append(ev)
            all_cases.extend(cases)
            exclusion_rows.append(
                {
                    "session_id": s["session_id"],
                    "rule_id": rule_id,
                    "prior_session_count": prior_n,
                    "excluded_symbols": ev["excluded_symbols"],
                    "excluded_symbol_count": ev["excluded_symbol_count"],
                }
            )
            if prior_n > 0:
                per_rule_oos_lists[rule_id].append(ev)
            if (
                rule_id != "A_no_filter"
                and prior_n >= 1
                and "20260520" in s["session_id"]
                and "5803.T" in excluded
            ):
                detect_5803 = True

        last_session_stats = _update_prior(prior_state, s["trades"])

    grid_summary: list[dict[str, Any]] = []
    for rule_id, _ in RULES:
        agg = _aggregate_oos(per_rule_oos_lists[rule_id])
        grid_summary.append({"rule_id": rule_id, **agg})

    recommendation, rec_detail = _recommend(
        grid_summary, session_count=len(sessions), detect_5803=detect_5803
    )

    review = {
        "phase": 78,
        "mode": "rolling_oos_symbol_cooloff",
        "output_dir": str(OUTPUT_SESSION),
        "sessions_chronological": [s["session_id"] for s in sessions],
        "session_count": len(sessions),
        "oos_method": "session_t exclusion uses only prior sessions (never same-session fit)",
        "constraints": {
            "no_same_day_fit_on_target_session": True,
            "no_production_code_change": True,
            "diagnosis_only": True,
        },
        "baseline": {
            "exit_policy": "combined_structural_exit_v1",
            "overlap": "current_overlap_replace",
        },
        "oos_cooloff_grid_summary": grid_summary,
        "oos_cooloff_by_session": [
            {k: v for k, v in r.items() if k != "_kept_pnls"}
            for r in oos_detail_rows
            if int(r.get("prior_session_count") or 0) > 0
        ],
        "5803_oos_analysis": _build_5803_analysis(perf_rows, exclusion_rows),
        "recommendation": recommendation,
        "recommendation_detail": rec_detail,
        "overfit_guard": {
            "same_session_filter_forbidden": True,
            "min_sessions_recommended": 4,
            "actual_sessions": len(sessions),
        },
    }

    out_json = OUTPUT_SESSION / "phase78_rolling_symbol_cooloff_review.json"
    out_perf = OUTPUT_SESSION / "phase78_session_symbol_performance.csv"
    out_grid = OUTPUT_SESSION / "phase78_oos_cooloff_grid.csv"
    out_cases = OUTPUT_SESSION / "phase78_oos_cooloff_cases.csv"
    out_excl = OUTPUT_SESSION / "phase78_exclusion_lists_by_session.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    if perf_rows:
        with out_perf.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(perf_rows[0].keys()))
            w.writeheader()
            w.writerows(perf_rows)

    if grid_summary:
        with out_grid.open("w", encoding="utf-8", newline="") as f:
            fields = list(grid_summary[0].keys())
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(grid_summary)
        detail_clean = [{k: v for k, v in r.items() if k != "_kept_pnls"} for r in oos_detail_rows]
        with out_grid.with_name("phase78_oos_cooloff_grid_detail.csv").open(
            "w", encoding="utf-8", newline=""
        ) as f:
            w = csv.DictWriter(f, fieldnames=list(detail_clean[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(detail_clean)

    if all_cases:
        with out_cases.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_cases[0].keys()))
            w.writeheader()
            w.writerows(all_cases)

    if exclusion_rows:
        with out_excl.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(exclusion_rows[0].keys()))
            w.writeheader()
            w.writerows(exclusion_rows)

    print("recommendation:", recommendation)
    print("sessions:", len(sessions), "5803_OOS:", detect_5803)
    for g in grid_summary:
        print(
            g["rule_id"],
            "OOS PF",
            g.get("aggregate_structural_pf"),
            "avoided",
            g.get("aggregate_avoided_losers"),
            "missed",
            g.get("aggregate_missed_winners"),
        )
    print("wrote", out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
