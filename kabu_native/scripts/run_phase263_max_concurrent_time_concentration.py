#!/usr/bin/env python3
"""
Phase263: When does max_concurrent concentrate? (review only)

Builds on Phase262: cap saturation clusters shortly after fills, not long sideways holds.

Output: kabu_native/results/reports/phase263_max_concurrent_time_concentration.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase263_max_concurrent_time_concentration.json"

TARGET_REASON = "max_concurrent"
V1_MODE = "legacy"
V1_RATIO = 0.85

ELAPSED_BUCKETS = (
    ("0_5min", 0.0, 5.0),
    ("5_10min", 5.0, 10.0),
    ("10_15min", 10.0, 15.0),
    ("15_30min", 15.0, 30.0),
    ("ge_30min", 30.0, None),
)


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


def _infer_kind(path: Path) -> str:
    s = str(path).replace("\\", "/").lower()
    if "/results/replay/" in s:
        return "replay"
    if "push_replay" in s:
        return "push_replay"
    if "live" in s:
        return "live"
    return "unknown"


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


def _clock_bucket_5m(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "unknown"
    total = dt.hour * 60 + dt.minute
    b = (total // 5) * 5
    return f"{b // 60:02d}:{b % 60:02d}"


def _elapsed_bucket(elapsed_min: float) -> str:
    for name, lo, hi in ELAPSED_BUCKETS:
        if hi is None and elapsed_min >= lo:
            return name
        if hi is not None and lo <= elapsed_min < hi:
            return name
    return "0_5min"


@dataclass
class BucketAgg:
    candidate_count: int = 0
    accepted_count: int = 0
    max_concurrent_count: int = 0
    rejected_count: int = 0
    accepted_pnls: list[float] = field(default_factory=list)
    mc_quality_sum: float = 0.0
    mc_quality_n: int = 0


def _finalize_bucket(agg: BucketAgg) -> dict[str, Any]:
    cand = agg.candidate_count
    mc = agg.max_concurrent_count
    rej = agg.rejected_count
    pnls = agg.accepted_pnls
    return {
        "candidate_count": cand,
        "accepted_count": agg.accepted_count,
        "max_concurrent_count": mc,
        "rejected_count": rej,
        "max_concurrent_rate": round(mc / cand, 4) if cand else None,
        "reject_rate": round(rej / cand, 4) if cand else None,
        "accepted_trade_count_for_pnl": len(pnls),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 6) if pnls else None,
        "profit_factor": _pf(pnls) if pnls else None,
        "total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
    }


def _session_start_ts(events: list[dict[str, Any]]) -> float:
    best = None
    for ev in events:
        for key in ("event_time", "entry_time"):
            ts = _parse_ts(str(ev.get(key) or ""))
            if ts <= 0:
                continue
            if best is None or ts < best:
                best = ts
    return best or 0.0


def _accepted_pnl_map(events: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
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
    out: dict[tuple[str, str], float] = {}
    for key, acc in accepts.items():
        ex = exits.get(key)
        pnl = _float(ex.get("pnl_pct")) if ex else _float(acc.get("pnl_pct"))
        if pnl is not None:
            out[key] = float(pnl)
    return out


def _process_session(
    events: list[dict[str, Any]],
    *,
    clock_aggs: dict[str, BucketAgg],
    elapsed_aggs: dict[str, BucketAgg],
    by_kind_clock: dict[str, dict[str, BucketAgg]],
    by_kind_elapsed: dict[str, dict[str, BucketAgg]],
    source_kind: str,
) -> None:
    if not events:
        return
    start_ts = _session_start_ts(events)
    pnl_map = _accepted_pnl_map(events)

    def bump(agg: BucketAgg, ev: dict[str, Any], ent: str) -> None:
        et = str(ev.get("event_type") or "")
        reason = str(ev.get("gate_reject_reason") or "")
        if et == "candidate":
            agg.candidate_count += 1
        elif et == "accepted":
            agg.accepted_count += 1
            key = (str(ev.get("symbol") or ""), ent)
            if key in pnl_map:
                agg.accepted_pnls.append(pnl_map[key])
        elif et == "rejected":
            agg.rejected_count += 1
            if reason == TARGET_REASON:
                agg.max_concurrent_count += 1
                q = _float(ev.get("continuation_quality_score"))
                if q is not None:
                    agg.mc_quality_sum += float(q)
                    agg.mc_quality_n += 1

    for ev in events:
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not ent:
            continue
        clk = _clock_bucket_5m(ent)
        el_min = (_parse_ts(ent) - start_ts) / 60.0 if start_ts > 0 else 0.0
        elb = _elapsed_bucket(el_min)

        for store, key in ((clock_aggs, clk), (elapsed_aggs, elb)):
            if key not in store:
                store[key] = BucketAgg()
            bump(store[key], ev, ent)

        kc = by_kind_clock.setdefault(source_kind, {})
        ke = by_kind_elapsed.setdefault(source_kind, {})
        if clk not in kc:
            kc[clk] = BucketAgg()
        if elb not in ke:
            ke[elb] = BucketAgg()
        bump(kc[clk], ev, ent)
        bump(ke[elb], ev, ent)


def _replay_mc_subset(
    p71: Any, events: list[dict[str, Any]], keys: set[tuple[str, str]]
) -> list[float]:
    if not keys:
        return []
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    pnls: list[float] = []
    inject: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("gate_reject_reason") or "") != TARGET_REASON:
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        key = (sym, ent)
        if key not in keys or key in inject:
            continue
        px = _float(ev.get("current_price"))
        if px and px > 0:
            inject[key] = ev

    injected: set[tuple[str, str]] = set()

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        pnls.append(float(p71._pnl_pct(act.trade.entry_price, close_price)))

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
                close_act(old, close_time=ent, close_price=price, reason="overlap_replaced_review")
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(sym, ent, price, float(inject[key].get("continuation_quality_score") or 0))
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
                close_act(act, close_time=ent, close_price=price, reason=str(reason))
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")
    return pnls


def _top_window_features(
    top_windows: list[str],
    *,
    sessions: list[dict[str, Any]],
    p71: Any,
) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    top_set = set(top_windows)
    mc_keys_by_session: dict[str, set[tuple[str, str]]] = defaultdict(set)
    qualities: list[float] = []
    scores_v1: list[float] = []
    scores_v2: list[float] = []
    accepted_pnls: list[float] = []
    mc_pnls_all: list[float] = []

    for sess in sessions:
        sdir = Path(sess["session_dir"])
        events = _load_events(sdir)
        if not events:
            continue
        sid = sess["session_id"]
        pnl_map = _accepted_pnl_map(events)
        for ev in events:
            ent = str(ev.get("entry_time") or ev.get("event_time") or "")
            if not ent or _clock_bucket_5m(ent) not in top_set:
                continue
            et = str(ev.get("event_type") or "")
            if et == "accepted":
                key = (str(ev.get("symbol") or ""), ent)
                if key in pnl_map:
                    accepted_pnls.append(pnl_map[key])
            if et == "rejected" and str(ev.get("gate_reject_reason") or "") == TARGET_REASON:
                q = _float(ev.get("continuation_quality_score"))
                if q is not None:
                    qualities.append(float(q))
                sf = compute_entry_expectancy_score_fields(trade=ev)
                v1 = _int(sf.get("entry_expectancy_score"))
                v2 = _int(sf.get("entry_expectancy_score_v2"))
                if v1 is not None:
                    scores_v1.append(float(v1))
                if v2 is not None:
                    scores_v2.append(float(v2))
                sym = str(ev.get("symbol") or "")
                mc_keys_by_session[sid].add((sym, ent))

    for sess in sessions:
        sid = sess["session_id"]
        keys = mc_keys_by_session.get(sid)
        if not keys:
            continue
        events = _load_events(Path(sess["session_dir"]))
        mc_pnls_all.extend(_replay_mc_subset(p71, events, keys))

    return {
        "top_windows": top_windows,
        "mc_event_count_in_windows": sum(len(v) for v in mc_keys_by_session.values()),
        "average_quality": round(statistics.mean(qualities), 4) if qualities else None,
        "average_entry_score": round(statistics.mean(scores_v1), 4) if scores_v1 else None,
        "average_entry_score_v2": round(statistics.mean(scores_v2), 4) if scores_v2 else None,
        "accepted_side": {
            "trade_count": len(accepted_pnls),
            "avg_pnl_pct": round(statistics.mean(accepted_pnls), 6) if accepted_pnls else None,
            "profit_factor": _pf(accepted_pnls) if accepted_pnls else None,
            "total_pnl_pct": round(sum(accepted_pnls), 4) if accepted_pnls else 0.0,
        },
        "max_concurrent_virtual": {
            "trade_count": len(mc_pnls_all),
            "avg_pnl_pct": round(statistics.mean(mc_pnls_all), 6) if mc_pnls_all else None,
            "profit_factor": _pf(mc_pnls_all) if mc_pnls_all else None,
            "total_pnl_pct": round(sum(mc_pnls_all), 4) if mc_pnls_all else 0.0,
            "method": "Phase245 replay subset for mc rejects in top windows only",
        },
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_module("phase71_p263", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    clock_aggs: dict[str, BucketAgg] = {}
    elapsed_aggs: dict[str, BucketAgg] = {}
    by_kind_clock: dict[str, dict[str, BucketAgg]] = {}
    by_kind_elapsed: dict[str, dict[str, BucketAgg]] = {}
    sessions: list[dict[str, Any]] = []

    if SMALL_PAPER.is_dir():
        for summary in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
            sdir = summary.parent
            events = _load_events(sdir)
            kind = _infer_kind(sdir)
            sessions.append(
                {
                    "session_id": sdir.relative_to(SMALL_PAPER).as_posix(),
                    "session_dir": str(sdir),
                    "source_kind": kind,
                    "event_count": len(events),
                }
            )
            _process_session(
                events,
                clock_aggs=clock_aggs,
                elapsed_aggs=elapsed_aggs,
                by_kind_clock=by_kind_clock,
                by_kind_elapsed=by_kind_elapsed,
                source_kind=kind,
            )

    clock_rows = [
        {"window_5m": k, **_finalize_bucket(v), "mc_quality_avg": round(v.mc_quality_sum / v.mc_quality_n, 4) if v.mc_quality_n else None}
        for k, v in clock_aggs.items()
        if k != "unknown"
    ]
    clock_rows.sort(key=lambda r: (-int(r["max_concurrent_count"]), r["window_5m"]))
    top20 = clock_rows[:20]

    elapsed_out = {
        name: _finalize_bucket(elapsed_aggs.get(name, BucketAgg()))
        for name, _, _ in ELAPSED_BUCKETS
    }
    for name in elapsed_aggs:
        if name not in elapsed_out:
            elapsed_out[name] = _finalize_bucket(elapsed_aggs[name])

    print("Computing top-window features (virtual mc replay)...", flush=True)
    top_feature = _top_window_features(
        [r["window_5m"] for r in top20],
        sessions=sessions,
        p71=p71,
    )

    report = {
        "phase": 263,
        "mode": "max_concurrent_time_concentration",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "entry_changed": False,
            "universe_changed": False,
            "exit_changed": False,
            "yaml_changed": False,
        },
        "context": {
            "phase262_finding": "mean hold at max_concurrent ~1.36min; likely candidate burst not long slot hogging",
            "sessions_with_events": len(sessions),
            "population": "small_paper live + push_replay (events); replay has no event stream in this pipeline",
        },
        "1_clock_5m_buckets": {
            "all_sources": clock_rows,
            "by_source_kind": {
                kind: [
                    {"window_5m": k, **_finalize_bucket(a)}
                    for k, a in sorted(aggs.items(), key=lambda x: (-x[1].max_concurrent_count, x[0]))
                    if k != "unknown"
                ]
                for kind, aggs in by_kind_clock.items()
            },
        },
        "2_session_elapsed_buckets": {
            "all_sources": elapsed_out,
            "by_source_kind": {
                kind: {
                    name: _finalize_bucket(aggs.get(name, BucketAgg()))
                    for name, _, _ in ELAPSED_BUCKETS
                }
                for kind, aggs in by_kind_elapsed.items()
            },
        },
        "3_top20_max_concurrent_windows": top20,
        "4_top_window_characteristics": top_feature,
        "summary": {
            "total_max_concurrent": sum(a.max_concurrent_count for a in clock_aggs.values()),
            "total_candidates": sum(a.candidate_count for a in clock_aggs.values()),
            "peak_window_5m": top20[0]["window_5m"] if top20 else None,
            "peak_window_mc_count": top20[0]["max_concurrent_count"] if top20 else 0,
            "elapsed_peak_bucket": max(
                ELAPSED_BUCKETS,
                key=lambda b: elapsed_aggs.get(b[0], BucketAgg()).max_concurrent_count,
            )[0],
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    if top20:
        print(f"peak window {top20[0]['window_5m']} mc={top20[0]['max_concurrent_count']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
