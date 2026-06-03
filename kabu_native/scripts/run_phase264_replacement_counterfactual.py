#!/usr/bin/env python3
"""
Phase264: Rejected max_concurrent candidate vs occupying slots (review only).

Compare each max_concurrent reject to the up-to-3 symbols holding gate slots at that instant.

Output: kabu_native/results/reports/phase264_replacement_counterfactual.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase264_replacement_counterfactual.json"

TARGET_REASON = "max_concurrent"
V1_MODE = "legacy"
V1_RATIO = 0.85
SAMPLE_EXAMPLES = 30


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel: str) -> Any:
    path = REPO / rel
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


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return (price / entry - 1.0) * 100.0


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


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


def _enrich_scores(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    q = _float(ev.get("continuation_quality_score"))
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
    }


def _final_outcomes(session_dir: Path, events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    st_path = session_dir / "structural_trades.csv"
    if st_path.is_file():
        with st_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = str(row.get("symbol") or "")
                ent = str(row.get("entry_time") or "")
                if not sym or not ent:
                    continue
                pnl = _float(row.get("realized_pnl_pct"))
                if pnl is None:
                    continue
                out[(sym, ent)] = {
                    "final_pnl_pct": float(pnl),
                    "mfe_pct": _float(row.get("mfe_pct")) or 0.0,
                    "mae_pct": abs(_float(row.get("mae_pct")) or 0.0),
                    "hold_min": (_float(row.get("hold_duration_sec")) or 0.0) / 60.0,
                }
        return out

    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    exits: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        et = str(ev.get("event_type") or "")
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if not key[0] or not key[1]:
            continue
        if et == "accepted":
            accepts[key] = ev
        elif et == "observer_exit":
            exits[key] = ev
    for key, acc in accepts.items():
        ex = exits.get(key)
        pnl = _float(ex.get("pnl_pct")) if ex else _float(acc.get("pnl_pct"))
        if pnl is None:
            continue
        ent_ts = _parse_ts(key[1])
        ex_ts = _parse_ts(str(ex.get("entry_time") or key[1])) if ex else ent_ts
        out[key] = {
            "final_pnl_pct": float(pnl),
            "mfe_pct": _float(acc.get("peak_mfe_pct")) or _float(acc.get("rolling_mfe_pct")) or 0.0,
            "mae_pct": abs(_float(acc.get("rolling_mae_pct")) or 0.0),
            "hold_min": max(0.0, (ex_ts - ent_ts) / 60.0),
        }
    return out


def _collect_mc_snapshots(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return []

    ordered = sorted(
        events,
        key=lambda ev: (
            _parse_ts(str(ev.get("entry_time") or ev.get("event_time") or "")),
            int(_float(ev.get("message_index")) or 0),
        ),
    )
    gate_slots: list[tuple[float, float, str]] = []
    positions: dict[str, dict[str, Any]] = {}
    snapshots: list[dict[str, Any]] = []

    for ev in ordered:
        sym = str(ev.get("symbol") or "")
        ent_raw = str(ev.get("entry_time") or ev.get("event_time") or "")
        ent_ts = _parse_ts(ent_raw)
        ex_ts = _parse_ts(str(ev.get("exit_time") or "")) or ent_ts + 3600.0
        price = _float(ev.get("current_price")) or 0.0
        et = str(ev.get("event_type") or "")
        reason = str(ev.get("gate_reject_reason") or "")

        if sym and price > 0 and sym in positions:
            pos = positions[sym]
            if ent_ts >= pos["entry_ts"]:
                x = _pnl_pct(pos["entry_price"], price)
                pos["mfe"] = max(pos["mfe"], x)
                pos["mae"] = min(pos["mae"], x)
                pos["unrealized"] = x

        gate_slots = [(a, b, s) for a, b, s in gate_slots if b >= ent_ts]

        if et == "rejected" and reason == TARGET_REASON and price > 0:
            rs = _enrich_scores(ev)
            open_rows: list[dict[str, Any]] = []
            seen_syms: set[str] = set()
            for _a, _b, s in gate_slots:
                if s in seen_syms:
                    continue
                seen_syms.add(s)
                pos = positions.get(s)
                if not pos:
                    continue
                hold_min = max(0.0, (ent_ts - pos["entry_ts"]) / 60.0)
                open_rows.append(
                    {
                        "symbol": s,
                        "entry_time": pos["entry_time"],
                        "hold_min": round(hold_min, 2),
                        "unrealized_pnl_pct": round(float(pos.get("unrealized") or 0.0), 4),
                        "mfe_pct": round(float(pos.get("mfe") or 0.0), 4),
                        "mae_pct": round(float(pos.get("mae") or 0.0), 4),
                        "quality": pos.get("quality"),
                        "entry_score": pos.get("entry_score"),
                        "entry_score_v2": pos.get("entry_score_v2"),
                    }
                )
            snapshots.append(
                {
                    "reject_key": (sym, str(ev.get("entry_time") or ent_raw)),
                    "reject_event_time": ent_raw,
                    "reject_symbol": sym,
                    "reject_quality": rs["quality"],
                    "reject_entry_score": rs["entry_score"],
                    "reject_entry_score_v2": rs["entry_score_v2"],
                    "open_positions": open_rows,
                    "open_slot_count": len(open_rows),
                }
            )

        if et == "accepted" and sym and price > 0:
            sc = _enrich_scores(ev)
            gate_slots.append((ent_ts, ex_ts, sym))
            positions[sym] = {
                "entry_time": str(ev.get("entry_time") or ent_raw),
                "entry_ts": ent_ts,
                "exit_ts": ex_ts,
                "entry_price": price,
                "mfe": 0.0,
                "mae": 0.0,
                "unrealized": 0.0,
                "quality": sc["quality"],
                "entry_score": sc["entry_score"],
                "entry_score_v2": sc["entry_score_v2"],
            }
        # Do not drop positions on observer_exit: ExposureGate slots persist until exit_time.

    return snapshots


def _replay_virtual_mc_metrics(p71: Any, events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    metrics: dict[tuple[str, str], dict[str, Any]] = {}
    inject: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("gate_reject_reason") or "") != TARGET_REASON:
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        if not sym or not ent:
            continue
        key = (sym, ent)
        if key in inject:
            continue
        px = _float(ev.get("current_price"))
        if px and px > 0:
            inject[key] = ev

    injected: set[tuple[str, str]] = set()

    def close_act(act: Any, key: tuple[str, str], *, close_time: str, close_price: float, reason: str) -> None:
        pnls = [float(t.get("pnl_pct") or 0) for t in act.rich_ticks]
        pnl = float(p71._pnl_pct(act.trade.entry_price, close_price))
        metrics[key] = {
            "virtual_pnl_pct": round(pnl, 4),
            "virtual_mfe_pct": round(max(pnls) if pnls else 0.0, 4),
            "virtual_mae_pct": round(min(pnls) if pnls else 0.0, 4),
            "virtual_close_reason": str(reason),
        }

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
                ok = (old.trade.symbol, old.trade.entry_time)
                close_act(old, ok, close_time=ent, close_price=price, reason="overlap_replaced_review")
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(
                sym,
                ent,
                price,
                float(inject[key].get("continuation_quality_score") or 0),
            )
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
        if str(ev.get("event_type") or "") == "candidate" and sym in active:
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
                k2 = (act.trade.symbol, act.trade.entry_time)
                close_act(act, k2, close_time=ent, close_price=price, reason=str(reason))
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        k2 = (act.trade.symbol, act.trade.entry_time)
        close_act(act, k2, close_time=session_end, close_price=float(last_px), reason="session_end")

    return metrics


@dataclass
class CompareAgg:
    n: int = 0
    n_with_full_slots: int = 0
    better_than_weakest_final: int = 0
    better_than_avg_open_final: int = 0
    better_than_weakest_unrealized: int = 0
    swap_deltas: list[float] = field(default_factory=list)
    rule_score_replace: int = 0
    rule_score_improve_sum: float = 0.0
    rule_quality_replace: int = 0
    rule_quality_improve_sum: float = 0.0
    rule_unrealized_replace: int = 0
    rule_unrealized_improve_sum: float = 0.0
    rule_holdtime_replace: int = 0
    rule_holdtime_improve_sum: float = 0.0


def _compare_snapshot(
    snap: dict[str, Any],
    virtual: dict[str, Any],
    finals: dict[tuple[str, str], dict[str, Any]],
    aggs: list[CompareAgg],
) -> Optional[dict[str, Any]]:
    key = tuple(snap["reject_key"])
    vm = virtual.get(key)
    if not vm:
        return None
    opens = snap.get("open_positions") or []
    if not opens:
        return None

    for o in opens:
        fk = (o["symbol"], o["entry_time"])
        fin = finals.get(fk)
        if fin:
            o["final_pnl_pct"] = fin["final_pnl_pct"]
            o["final_mfe_pct"] = fin.get("mfe_pct")
            o["final_mae_pct"] = fin.get("mae_pct")
            o["final_hold_min"] = fin.get("hold_min")
        else:
            o["final_pnl_pct"] = None

    open_finals = [o["final_pnl_pct"] for o in opens if o.get("final_pnl_pct") is not None]
    open_unrl = [float(o["unrealized_pnl_pct"]) for o in opens]
    rej_pnl = float(vm["virtual_pnl_pct"])

    weakest_final = min(open_finals) if open_finals else None
    avg_final = statistics.mean(open_finals) if open_finals else None
    weakest_unrl = min(open_unrl) if open_unrl else None

    rej_score = snap.get("reject_entry_score")
    rej_q = snap.get("reject_quality")
    open_scores = [o.get("entry_score") for o in opens if o.get("entry_score") is not None]
    open_qs = [o.get("quality") for o in opens if o.get("quality") is not None]
    long_holder = max(opens, key=lambda o: float(o.get("hold_min") or 0))
    long_final = long_holder.get("final_pnl_pct")

    for agg in aggs:
        agg.n += 1
        if len(opens) >= 3:
            agg.n_with_full_slots += 1
        if weakest_final is not None and rej_pnl > weakest_final:
            agg.better_than_weakest_final += 1
        if avg_final is not None and rej_pnl > avg_final:
            agg.better_than_avg_open_final += 1
        if weakest_unrl is not None and rej_pnl > weakest_unrl:
            agg.better_than_weakest_unrealized += 1
        if weakest_final is not None:
            agg.swap_deltas.append(rej_pnl - weakest_final)
        if rej_score is not None and open_scores and float(rej_score) > min(float(x) for x in open_scores):
            agg.rule_score_replace += 1
            if weakest_final is not None:
                agg.rule_score_improve_sum += rej_pnl - weakest_final
        if rej_q is not None and open_qs and float(rej_q) > min(float(x) for x in open_qs):
            agg.rule_quality_replace += 1
            if weakest_final is not None:
                agg.rule_quality_improve_sum += rej_pnl - weakest_final
        if weakest_unrl is not None and rej_pnl > weakest_unrl:
            agg.rule_unrealized_replace += 1
            if weakest_final is not None:
                agg.rule_unrealized_improve_sum += rej_pnl - weakest_final
        if long_final is not None and rej_pnl > float(long_final):
            agg.rule_holdtime_replace += 1
            agg.rule_holdtime_improve_sum += rej_pnl - float(long_final)

    return {
        "reject": {
            "symbol": snap["reject_symbol"],
            "event_time": snap["reject_event_time"],
            "quality": snap.get("reject_quality"),
            "entry_score": snap.get("reject_entry_score"),
            "entry_score_v2": snap.get("reject_entry_score_v2"),
            **vm,
        },
        "open_positions": opens,
        "comparison": {
            "reject_better_than_weakest_final": bool(weakest_final is not None and rej_pnl > weakest_final),
            "reject_better_than_avg_open_final": bool(avg_final is not None and rej_pnl > avg_final),
            "reject_better_than_weakest_unrealized": bool(weakest_unrl is not None and rej_pnl > weakest_unrl),
            "swap_delta_vs_weakest_final_pnl": round(rej_pnl - weakest_final, 4) if weakest_final is not None else None,
            "weakest_open_final_pnl": weakest_final,
            "avg_open_final_pnl": round(avg_final, 4) if avg_final is not None else None,
        },
    }


def _finalize_agg(agg: CompareAgg) -> dict[str, Any]:
    n = agg.n or 1
    return {
        "comparison_count": agg.n,
        "with_at_least_one_open_slot": agg.n,
        "with_three_open_slots": agg.n_with_full_slots,
        "reject_better_than_weakest_final_pct": round(100.0 * agg.better_than_weakest_final / n, 2),
        "reject_better_than_avg_open_final_pct": round(100.0 * agg.better_than_avg_open_final / n, 2),
        "reject_better_than_weakest_unrealized_pct": round(100.0 * agg.better_than_weakest_unrealized / n, 2),
        "swap_delta_vs_weakest_final": {
            "mean": round(statistics.mean(agg.swap_deltas), 4) if agg.swap_deltas else None,
            "median": round(statistics.median(agg.swap_deltas), 4) if agg.swap_deltas else None,
            "sum": round(sum(agg.swap_deltas), 4) if agg.swap_deltas else 0.0,
        },
        "replacement_rules_counterfactual": {
            "by_entry_score_gt_min_open": {
                "would_replace_count": agg.rule_score_replace,
                "would_replace_pct": round(100.0 * agg.rule_score_replace / n, 2),
                "pnl_delta_vs_weakest_final_sum": round(agg.rule_score_improve_sum, 4),
            },
            "by_quality_gt_min_open": {
                "would_replace_count": agg.rule_quality_replace,
                "would_replace_pct": round(100.0 * agg.rule_quality_replace / n, 2),
                "pnl_delta_vs_weakest_final_sum": round(agg.rule_quality_improve_sum, 4),
            },
            "by_virtual_pnl_gt_weakest_unrealized": {
                "would_replace_count": agg.rule_unrealized_replace,
                "would_replace_pct": round(100.0 * agg.rule_unrealized_replace / n, 2),
                "pnl_delta_vs_weakest_final_sum": round(agg.rule_unrealized_improve_sum, 4),
            },
            "by_virtual_pnl_gt_longest_hold_open": {
                "would_replace_count": agg.rule_holdtime_replace,
                "would_replace_pct": round(100.0 * agg.rule_holdtime_replace / n, 2),
                "pnl_delta_vs_that_slot_final_sum": round(agg.rule_holdtime_improve_sum, 4),
            },
        },
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_module("phase71_p264", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    agg_all = CompareAgg()
    agg_by_kind: dict[str, CompareAgg] = defaultdict(CompareAgg)
    examples: list[dict[str, Any]] = []
    sessions_n = 0

    if not SMALL_PAPER.is_dir():
        OUT.write_text("{}\n", encoding="utf-8")
        return 0

    for summary in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sdir = summary.parent
        events = _load_events(sdir)
        if not events:
            continue
        sessions_n += 1
        kind = "live" if "live" in str(sdir).lower() else "push_replay" if "push_replay" in str(sdir).lower() else "unknown"
        finals = _final_outcomes(sdir, events)
        snapshots = _collect_mc_snapshots(events)
        if not snapshots:
            continue
        virtual = _replay_virtual_mc_metrics(p71, events)
        for snap in snapshots:
            aggs = [agg_all, agg_by_kind[kind]]
            ex = _compare_snapshot(snap, virtual, finals, aggs)
            if ex and len(examples) < SAMPLE_EXAMPLES and random.random() < 0.0002:
                examples.append(ex)
        if sessions_n % 10 == 0:
            print(f"  sessions={sessions_n} comparisons={agg_all.n}", flush=True)

    if len(examples) < 10 and agg_all.n:
        examples = examples[:10]

    report = {
        "phase": 264,
        "mode": "replacement_counterfactual",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "entry_changed": False,
            "universe_changed": False,
            "exit_changed": False,
            "yaml_changed": False,
        },
        "method": {
            "open_slots": "ExposureGate open_slots at max_concurrent (max 3)",
            "reject_virtual": "Phase245 structural replay per (symbol, entry_time)",
            "open_final_pnl": "structural_trades.csv or accept+observer_exit",
            "weakest_definition_final": "min final_pnl_pct among occupying slots",
        },
        "population": {
            "sessions_processed": sessions_n,
            "note": "replay/ path has no event stream; live+push_replay only",
        },
        "3_comparison_summary": _finalize_agg(agg_all),
        "by_source_kind": {k: _finalize_agg(v) for k, v in agg_by_kind.items()},
        "sample_comparisons": examples[:SAMPLE_EXAMPLES],
        "verdict": (
            "rejected_often_beats_weakest_occupier"
            if agg_all.n
            and agg_all.better_than_weakest_final / agg_all.n > 0.5
            else "rejected_not_consistently_better_than_occupiers"
        ),
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} comparisons={agg_all.n}", flush=True)
    if agg_all.n:
        pct = 100.0 * agg_all.better_than_weakest_final / agg_all.n
        print(f"better_than_weakest_final={pct:.1f}%", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
