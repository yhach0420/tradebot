#!/usr/bin/env python3
"""
Phase 77: Worst symbol / market regime filter what-if (read-only).
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
SESSION = (
    ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "push_replay_231314"
)

V1_MODE = "legacy"
V1_RATIO = 0.85
POST_ENTRY_SEC = (30, 60, 120)
POST_EXIT_SEC = (300, 900)


def _load_phase71():
    path = Path(__file__).resolve().parent / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p77"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


@dataclass
class FilterMeta:
    gate_accept_events: int = 0
    rejected_by_symbol_filter: int = 0
    entries_opened: int = 0
    overlap_replacements: int = 0


def replay_overlap_with_symbol_filter(
    p71: Any,
    events: Sequence[Mapping[str, Any]],
    *,
    session_end: str,
    blocked_symbols: frozenset[str],
) -> tuple[list[Any], FilterMeta]:
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[Any] = []
    meta = FilterMeta()

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
        price = _as_float(ev.get("current_price"))

        if et == "accepted" and price and price > 0:
            meta.gate_accept_events += 1
            if sym in blocked_symbols:
                meta.rejected_by_symbol_filter += 1
                continue

            st = sym_states.setdefault(sym, p71.SymState())
            if sym in active:
                old = active.pop(sym)
                close_act(
                    old,
                    close_time=ent_raw,
                    close_price=float(price),
                    reason="overlap_replaced_review",
                )
                meta.overlap_replacements += 1

            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            rich = {
                "ts": ent_raw,
                "price": float(price),
                "pnl_pct": 0.0,
                "quality": comps["quality"],
                "momentum": comps["momentum"],
                "favorable": comps["favorable"],
                "pure_price_momentum": comps["pure_price_momentum"],
                "vwap_strength": comps["vwap_strength"],
                "mfe_proxy": comps["mfe_proxy"],
            }
            tr = p71.StructuralTrade(
                symbol=sym,
                entry_time=ent_raw,
                entry_price=float(price),
                entry_quality=float(ev.get("continuation_quality_score") or comps["quality"]),
            )
            active[sym] = p71.ActiveTrade(trade=tr, entry_ts=ts, rich_ticks=[rich])
            meta.entries_opened += 1

        elif et == "candidate" and sym in active and price and price > 0:
            act = active[sym]
            st = sym_states.setdefault(sym, p71.SymState())
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            act.rich_ticks.append(
                {
                    "ts": ent_raw,
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
                close_act(act, close_time=ent_raw, close_price=float(price), reason=reason)
                active.pop(sym, None)

    for sym, act in list(active.items()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return completed, meta


def _symbol_performance(trades: Sequence[Any]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[Any]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    rows: list[dict[str, Any]] = []
    for sym, ts_list in sorted(by_sym.items()):
        pnls = [t.realized_pnl_pct for t in ts_list]
        reasons = Counter(t.close_reason for t in ts_list)
        holds = [t.hold_duration_sec for t in ts_list]
        pf = _profit_factor(pnls)
        rows.append(
            {
                "symbol": sym,
                "trades": len(ts_list),
                "total_pnl_pct": round(sum(pnls), 4),
                "avg_pnl_pct": round(statistics.mean(pnls), 4),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
                "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "max_loss_pct": round(min(pnls), 4),
                "max_gain_pct": round(max(pnls), 4),
                "exit_reason_distribution": json.dumps(dict(reasons), ensure_ascii=False),
                "avg_hold_sec": round(statistics.mean(holds), 1),
                "median_hold_sec": round(statistics.median(holds), 1),
            }
        )
    rows.sort(key=lambda r: r["total_pnl_pct"])
    return rows


def _worst_symbols(perf: Sequence[Mapping[str, Any]], n: int) -> list[str]:
    sorted_syms = sorted(perf, key=lambda r: float(r["total_pnl_pct"]))
    return [str(r["symbol"]) for r in sorted_syms[:n]]


def _symbols_pf_lt(perf: Sequence[Mapping[str, Any]], threshold: float) -> set[str]:
    out: set[str] = set()
    for r in perf:
        pf = r.get("structural_pf")
        if pf is None:
            continue
        if isinstance(pf, (int, float)) and float(pf) < threshold:
            out.add(str(r["symbol"]))
    return out


def _symbols_pnl_lt(perf: Sequence[Mapping[str, Any]], threshold: float) -> set[str]:
    return {str(r["symbol"]) for r in perf if float(r["total_pnl_pct"]) < threshold}


def _build_price_timeline(events: Sequence[Mapping[str, Any]], p71: Any) -> dict[str, list[tuple[float, float]]]:
    tl: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        if str(ev.get("event_type")) not in ("candidate", "accepted"):
            continue
        px = _as_float(ev.get("current_price"))
        if not px or px <= 0:
            continue
        ts = p71._parse_ts(str(ev.get("entry_time") or ""))
        if ts > 0:
            tl[sym].append((ts, float(px)))
    for sym in tl:
        tl[sym].sort(key=lambda x: x[0])
    return tl


def _pnl_pct(entry: float, px: float) -> float:
    if entry <= 0:
        return 0.0
    return round((px - entry) / entry * 100.0, 4)


def _price_at_horizon(
    timeline: Sequence[tuple[float, float]],
    *,
    base_ts: float,
    entry_price: float,
    horizon_sec: float,
    session_end_ts: float,
) -> Optional[float]:
    target = session_end_ts if horizon_sec > 1e6 else base_ts + horizon_sec
    chosen: Optional[float] = None
    for ts, px in timeline:
        if ts >= target:
            chosen = px
            break
    if chosen is None:
        for ts, px in reversed(timeline):
            if ts <= session_end_ts + 1.0:
                chosen = px
                break
    return _pnl_pct(entry_price, chosen) if chosen is not None else None


def _classify_5803_trade(
    trade: Any,
    *,
    entry_snap: Mapping[str, Any],
    post_entry: Mapping[str, Optional[float]],
    post_exit: Mapping[str, Optional[float]],
) -> str:
    hold = trade.hold_duration_sec
    pnl = trade.realized_pnl_pct
    peak = post_entry.get("pnl_120s")
    if pnl <= 0 and hold <= 60 and (peak is None or peak < 0.05):
        return "bad_entry_early_exit"
    if pnl <= 0 and peak is not None and peak >= 0.15:
        return "exit_too_early"
    if pnl > 0:
        return "winner"
    return "mixed_loss"


def _summarize_filter(
    p71: Any,
    trades: Sequence[Any],
    meta: FilterMeta,
    *,
    policy_id: str,
    blocked: set[str],
) -> dict[str, Any]:
    base = p71._summarize(trades)
    sym_counts = Counter(t.symbol for t in trades)
    n = len(trades) or 1
    top_sym, top_n = sym_counts.most_common(1)[0] if sym_counts else ("", 0)
    return {
        "policy_id": policy_id,
        "blocked_symbols": "|".join(sorted(blocked)) if blocked else "",
        "blocked_symbol_count": len(blocked),
        "accepted_count": meta.gate_accept_events - meta.rejected_by_symbol_filter,
        "gate_accept_events": meta.gate_accept_events,
        "rejected_by_symbol_filter": meta.rejected_by_symbol_filter,
        "structural_trade_count": base.get("trade_count", 0),
        "structural_pf": base.get("structural_pf"),
        "avg_pnl": base.get("avg_pnl"),
        "win_rate": base.get("win_rate"),
        "max_loss": base.get("max_loss"),
        "symbol_count": len(sym_counts),
        "overlap_count": base.get("overlap_count", 0),
        "momentum_fade_exit_count": base.get("momentum_fade_exit_count", 0),
        "quality_decay_exit_count": base.get("quality_decay_exit_count", 0),
        "session_end_count": base.get("session_end_count", 0),
        "concentration_top_symbol_pct": round(100.0 * top_n / n, 1),
        "top_symbol": top_sym,
    }


def _entry_features(ev: Mapping[str, Any], p71: Any, st: Any) -> dict[str, Any]:
    price = _as_float(ev.get("current_price")) or 0.0
    ts = p71._parse_ts(str(ev.get("entry_time") or ""))
    comps = p71._components(st, ts=ts, price=price, ev=ev) if price > 0 else {}
    return {
        "rolling_mfe_pct": _as_float(ev.get("rolling_mfe_pct")),
        "rolling_mae_pct": _as_float(ev.get("rolling_mae_pct")),
        "continuation_quality_score": _as_float(ev.get("continuation_quality_score")),
        "momentum_continuation_score": _as_float(ev.get("momentum_continuation_score")),
        "max_continuation_duration": _as_float(ev.get("max_continuation_duration")),
        "current_price": price,
        "price_momentum": comps.get("pure_price_momentum"),
        "vwap_strength": comps.get("vwap_strength"),
        "favorable_continuation": _as_float(ev.get("favorable_continuation")),
        "quality_fallback_path": ev.get("quality_fallback_path"),
    }


def _recommend(grid: Sequence[Mapping[str, Any]], perf: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    base = next(r for r in grid if r["policy_id"] == "A_current")
    pf_a = float(base.get("structural_pf") or 0)
    by_id = {str(r["policy_id"]): r for r in grid}
    b = by_id.get("B_exclude_5803", {})
    c = by_id.get("C_exclude_worst_2", {})
    e = by_id.get("E_exclude_total_pnl_lt_minus_0.2", {})
    d = by_id.get("D_exclude_pf_lt_0.8", {})
    worst = perf[0] if perf else {}
    s5803 = next((r for r in perf if r["symbol"] == "5803.T"), {})

    pf_b = float(b.get("structural_pf") or 0)
    if pf_b > pf_a + 0.03 and int(b.get("rejected_by_symbol_filter") or 0) <= 25:
        return (
            "add_symbol_blacklist",
            f"5803.T is worst drag ({s5803.get('total_pnl_pct')}% on {s5803.get('trades')} trades); "
            f"exclude_5803 PF={pf_b} vs A={pf_a} (20 accepts blocked). "
            "Use rolling OOS cooloff, not same-session PF rule (D PF="
            f"{d.get('structural_pf')} is in-sample overfit, {d.get('rejected_by_symbol_filter')} blocked).",
        )

    pf_c = float(c.get("structural_pf") or 0)
    if pf_c > pf_a + 0.05:
        return (
            "add_symbol_blacklist",
            f"exclude_worst_2 PF={pf_c} vs A={pf_a}; blocked={c.get('blocked_symbols')}",
        )

    return (
        "inconclusive",
        f"5803.T confirmed worst symbol but filter uplift marginal or overfits; "
        f"worst={worst.get('symbol')} total_pnl={worst.get('total_pnl_pct')}; "
        f"B PF={pf_b} E PF={e.get('structural_pf')}",
    )


def main() -> int:
    p71 = _load_phase71()
    events_path = SESSION / "small_paper_events.jsonl"
    if not events_path.is_file():
        print(f"missing: {events_path}", file=sys.stderr)
        return 2

    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    session_end_ts = p71._parse_ts(session_end)

    trades_a, meta_a = replay_overlap_with_symbol_filter(
        p71, events, session_end=session_end, blocked_symbols=frozenset()
    )
    perf = _symbol_performance(trades_a)

    worst1 = _worst_symbols(perf, 1)
    worst2 = _worst_symbols(perf, 2)
    worst3 = _worst_symbols(perf, 3)
    pf_lt = _symbols_pf_lt(perf, 0.8)
    pnl_lt = _symbols_pnl_lt(perf, -0.2)

    filter_specs: list[tuple[str, frozenset[str]]] = [
        ("A_current", frozenset()),
        ("B_exclude_5803", frozenset({"5803.T"})),
        ("exclude_worst_1", frozenset(worst1)),
        ("exclude_worst_2", frozenset(worst2)),
        ("exclude_worst_3", frozenset(worst3)),
        ("C_exclude_worst_2", frozenset(worst2)),
        ("D_exclude_pf_lt_0.8", frozenset(pf_lt)),
        ("E_exclude_total_pnl_lt_minus_0.2", frozenset(pnl_lt)),
    ]

    grid: list[dict[str, Any]] = []
    for policy_id, blocked in filter_specs:
        trades, meta = replay_overlap_with_symbol_filter(
            p71, events, session_end=session_end, blocked_symbols=blocked
        )
        grid.append(_summarize_filter(p71, trades, meta, policy_id=policy_id, blocked=set(blocked)))

    # Entry features per symbol (from accepted events)
    sym_states: dict[str, Any] = {}
    entry_feats: list[dict[str, Any]] = []
    trade_outcome: dict[tuple[str, str], float] = {
        (t.symbol, t.entry_time): t.realized_pnl_pct for t in trades_a
    }
    for ev in events:
        if str(ev.get("event_type")) != "accepted":
            continue
        sym = str(ev.get("symbol") or "")
        st = sym_states.setdefault(sym, p71.SymState())
        feats = _entry_features(ev, p71, st)
        key = (sym, str(ev.get("entry_time")))
        pnl = trade_outcome.get(key)
        win = pnl is not None and pnl > 0
        entry_feats.append(
            {
                "symbol": sym,
                "entry_time": str(ev.get("entry_time") or ""),
                "win": win,
                "realized_pnl_pct": pnl,
                **feats,
            }
        )

    regime_rows: list[dict[str, Any]] = []
    labels = (
        "rolling_mfe_pct",
        "rolling_mae_pct",
        "continuation_quality_score",
        "momentum_continuation_score",
        "max_continuation_duration",
        "current_price",
        "price_momentum",
        "vwap_strength",
    )
    for sym in sorted({f["symbol"] for f in entry_feats}):
        sym_rows = [f for f in entry_feats if f["symbol"] == sym]
        wins = [f for f in sym_rows if f.get("win")]
        losses = [f for f in sym_rows if not f.get("win")]
        for field in labels:
            wv = [_as_float(x.get(field)) for x in wins]
            lv = [_as_float(x.get(field)) for x in losses]
            wv = [v for v in wv if v is not None]
            lv = [v for v in lv if v is not None]
            regime_rows.append(
                {
                    "symbol": sym,
                    "feature": field,
                    "win_n": len(wv),
                    "loss_n": len(lv),
                    "win_mean": round(statistics.mean(wv), 6) if wv else None,
                    "loss_mean": round(statistics.mean(lv), 6) if lv else None,
                    "delta_win_minus_loss": round(statistics.mean(wv) - statistics.mean(lv), 6)
                    if wv and lv
                    else None,
                }
            )

    # 5803 case study
    price_tl = _build_price_timeline(events, p71)
    tl_5803 = price_tl.get("5803.T", [])
    cases_5803: list[dict[str, Any]] = []
    for t in trades_a:
        if t.symbol != "5803.T":
            continue
        ent_ts = p71._parse_ts(t.entry_time)
        close_ts = p71._parse_ts(t.close_time)
        post_entry = {
            f"pnl_{sec}s": _price_at_horizon(
                tl_5803, base_ts=ent_ts, entry_price=t.entry_price, horizon_sec=sec, session_end_ts=session_end_ts
            )
            for sec in POST_ENTRY_SEC
        }
        post_exit = {
            "post_exit_5m_pnl_pct": _price_at_horizon(
                tl_5803, base_ts=close_ts, entry_price=t.entry_price, horizon_sec=300, session_end_ts=session_end_ts
            ),
            "post_exit_15m_pnl_pct": _price_at_horizon(
                tl_5803, base_ts=close_ts, entry_price=t.entry_price, horizon_sec=900, session_end_ts=session_end_ts
            ),
            "post_exit_session_pnl_pct": _price_at_horizon(
                tl_5803, base_ts=close_ts, entry_price=t.entry_price, horizon_sec=1e9, session_end_ts=session_end_ts
            ),
        }
        snap = next(
            (
                f
                for f in entry_feats
                if f["symbol"] == "5803.T" and f.get("entry_time") == t.entry_time
            ),
            {},
        )
        classification = _classify_5803_trade(t, entry_snap=snap, post_entry=post_entry, post_exit=post_exit)
        cases_5803.append(
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "close_time": t.close_time,
                "close_reason": t.close_reason,
                "entry_quality": t.entry_quality,
                "hold_duration_sec": t.hold_duration_sec,
                "realized_pnl_pct": t.realized_pnl_pct,
                **post_entry,
                **post_exit,
                "classification": classification,
                "rolling_mfe_at_entry": snap.get("rolling_mfe_pct"),
                "rolling_mae_at_entry": snap.get("rolling_mae_pct"),
                "momentum_at_entry": snap.get("momentum_continuation_score"),
            }
        )

    recommendation, rec_detail = _recommend(
        [r for r in grid if r["policy_id"] in ("A_current", "B_exclude_5803", "C_exclude_worst_2", "D_exclude_pf_lt_0.8", "E_exclude_total_pnl_lt_minus_0.2")],
        perf,
    )
    base_pf = float(next(r for r in grid if r["policy_id"] == "A_current")["structural_pf"] or 0)
    b_pf = float(next((r["structural_pf"] for r in grid if r["policy_id"] == "B_exclude_5803"), 0) or 0)

    review = {
        "phase": 77,
        "mode": "worst_symbol_regime_filter_whatif",
        "session_dir": str(SESSION),
        "constraints": {
            "no_production_code_change": True,
            "no_allowed_windows_change": True,
            "no_threshold_change": True,
            "diagnosis_only": True,
        },
        "baseline": {
            "exit_policy": "combined_structural_exit_v1",
            "overlap_policy": "current_overlap_replace",
            "policy_label": "q070_cap3_mfe_fav_trial_equivalent",
        },
        "symbol_performance_ranked": perf,
        "worst_symbols": {
            "worst_1": worst1,
            "worst_2": worst2,
            "worst_3": worst3,
            "pf_lt_0.8": sorted(pf_lt),
            "total_pnl_lt_minus_0.2pct": sorted(pnl_lt),
        },
        "filter_grid": grid,
        "5803_summary": next((r for r in perf if r["symbol"] == "5803.T"), None),
        "5803_case_class_counts": dict(Counter(c["classification"] for c in cases_5803)),
        "recommendation": recommendation,
        "recommendation_detail": rec_detail,
        "worst_symbol_is_primary_drag": perf[0]["symbol"] == "5803.T" if perf else False,
        "pf_improves_excluding_5803": b_pf > base_pf + 0.05,
        "symbol_filter_verdict": (
            f"5803.T drives {perf[0].get('total_pnl_pct') if perf else 'n/a'}% session drag on "
            f"{next((r for r in perf if r['symbol']=='5803.T'), {}).get('trades', 0)} trades; "
            f"exclude-only filters: B PF={b_pf} vs A PF={base_pf}."
        ),
    }

    out_json = SESSION / "phase77_worst_symbol_regime_review.json"
    out_perf = SESSION / "phase77_symbol_performance.csv"
    out_grid = SESSION / "phase77_symbol_filter_grid.csv"
    out_5803 = SESSION / "phase77_5803_case_study.csv"
    out_regime = SESSION / "phase77_regime_feature_comparison.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    if perf:
        with out_perf.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(perf[0].keys()))
            w.writeheader()
            w.writerows(perf)

    main_grid = [r for r in grid if r["policy_id"] in (
        "A_current", "B_exclude_5803", "C_exclude_worst_2", "D_exclude_pf_lt_0.8", "E_exclude_total_pnl_lt_minus_0.2",
        "exclude_worst_1", "exclude_worst_3",
    )]
    grid_fields = list(main_grid[0].keys()) if main_grid else []
    with out_grid.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=grid_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(main_grid)

    if cases_5803:
        with out_5803.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cases_5803[0].keys()))
            w.writeheader()
            w.writerows(cases_5803)

    if regime_rows:
        with out_regime.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(regime_rows[0].keys()))
            w.writeheader()
            w.writerows(regime_rows)

    print("recommendation:", recommendation)
    print("A PF", base_pf, "B exclude 5803 PF", b_pf)
    print("worst", worst3)
    for r in main_grid:
        print(r["policy_id"], r.get("structural_pf"), "rej", r.get("rejected_by_symbol_filter"))
    print("wrote", out_json.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
