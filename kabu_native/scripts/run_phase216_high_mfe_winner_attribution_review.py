#!/usr/bin/env python3
"""
Phase216: High-MFE winner attribution review (review only).

Compare entry-time features: MFE >= 3% (A) vs MFE < 1% (B).
All IS + OOS trades — not cohort-filtered.
"""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase216_high_mfe_winner_attribution_review.json"

MFE_A_MIN = 3.0
MFE_B_MAX = 1.0

FEATURES: tuple[tuple[str, str], ...] = (
    ("trading_value", "trading_value"),
    ("turnover_proxy", "turnover_proxy"),
    ("entry_vwap_dev_pct", "entry_vwap_dev_pct"),
    ("order_book_imbalance", "order_book_imbalance"),
    ("quality", "continuation_quality_score"),
    ("daytrade_suitability", "daytrade_suitability_score"),
    ("momentum_continuation", "momentum_continuation_score"),
    ("continuation_duration", "max_continuation_duration"),
    ("rise_5min", "entry_rise_5min_pct"),
    ("rise_10min", "entry_rise_10min_pct"),
)


def _load_phase213c_module() -> Any:
    path = REPO / "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py"
    name = "phase213c_loader_p216"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _vals(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get(key)
        if v is None or v == "":
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 6) if xs else None


def _median(xs: list[float]) -> Optional[float]:
    return round(statistics.median(xs), 6) if xs else None


def _stdev(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    return round(statistics.stdev(xs), 6)


def _cohen_d(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    pooled = math.sqrt(((len(a) - 1) * sa * sa + (len(b) - 1) * sb * sb) / (len(a) + len(b) - 2))
    if pooled <= 1e-12:
        return None
    return round((ma - mb) / pooled, 4)


def _compare_feature(
    label: str,
    key: str,
    a_rows: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    av = _vals(a_rows, key)
    bv = _vals(b_rows, key)
    ma, mb = _mean(av), _mean(bv)
    delta = round(ma - mb, 6) if ma is not None and mb is not None else None
    return {
        "feature": label,
        "field": key,
        "A_count_with_value": len(av),
        "B_count_with_value": len(bv),
        "A_mean": ma,
        "B_mean": mb,
        "A_median": _median(av),
        "B_median": _median(bv),
        "A_stdev": _stdev(av),
        "B_stdev": _stdev(bv),
        "delta_A_minus_B_mean": delta,
        "cohen_d": _cohen_d(av, bv),
        "A_higher_than_B": (ma is not None and mb is not None and ma > mb),
    }


def _price_before(ring: list[tuple[float, float]], ts: float, lookback_sec: float) -> Optional[float]:
    target = ts - lookback_sec
    found: Optional[float] = None
    for t, px in ring:
        if t <= target:
            found = px
        elif t > ts:
            break
    return found


def _rise_pct(entry_px: float, prior_px: Optional[float]) -> Optional[float]:
    if prior_px is None or prior_px <= 0 or entry_px <= 0:
        return None
    return round((entry_px - prior_px) / prior_px * 100.0, 4)


def _get_price_ring(
    mod: Any,
    ring_cache: dict[tuple[str, str], list[tuple[float, float]]],
    push_dir: Path,
    symbol: str,
) -> list[tuple[float, float]]:
    from small_paper.extended_entry_shadow import append_price_tick

    key = (str(push_dir), symbol.replace(".T", ""))
    if key in ring_cache:
        return ring_cache[key]
    sym = symbol.replace(".T", "")
    for name in (f"{symbol}.jsonl", f"{sym}.jsonl"):
        path = push_dir / name
        if path.is_file():
            break
    else:
        ring_cache[key] = []
        return []
    ring: list[tuple[float, float]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = mod._parse_ts(str(rec.get("recorded_at") or ""))
            payload = rec.get("payload") or {}
            px = mod._float(payload.get("CurrentPrice"))
            if px and px > 0:
                append_price_tick(ring, ts=ts, px=float(px))
    ring_cache[key] = ring
    return ring


def _slice_ring_to_entry(ring: list[tuple[float, float]], entry_ts: float) -> list[tuple[float, float]]:
    if not ring:
        return []
    ts_list = [t for t, _ in ring]
    idx = bisect_right(ts_list, entry_ts)
    return ring[:idx]


def _compute_rise_fields(
    ring: list[tuple[float, float]], entry_ts: float, entry_px: float
) -> tuple[Optional[float], Optional[float]]:
    rise_5 = _rise_pct(entry_px, _price_before(ring, entry_ts, 300))
    rise_10 = _rise_pct(entry_px, _price_before(ring, entry_ts, 600))
    return rise_5, rise_10


def _replay_mfe_map(p71: Any, session_dir: Path) -> dict[tuple[str, str], float]:
    """Peak favorable pnl%% during v1 replay hold (= path MFE)."""
    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        return {}
    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    mfe_out: dict[tuple[str, str], float] = {}

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        peak = max((float(t.get("pnl_pct") or 0.0) for t in act.rich_ticks), default=0.0)
        key = (str(act.trade.symbol), str(act.trade.entry_time))
        mfe_out[key] = round(max(0.0, peak), 4)
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = p71._pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, p71._parse_ts(close_time) - act.entry_ts), 1)

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
                momentum_mode="legacy",
                ratio=0.85,
                allow_session_end=False,
            )
            if sig:
                close_act(act, close_time=ent_raw, close_price=price, reason=sig[1])
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return mfe_out


def _csv_mfe_maps(
    mod: Any, sdir: Path
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    mfe_map: dict[tuple[str, str], float] = {}
    px_map: dict[tuple[str, str], float] = {}
    for csv_name in ("structural_trades.csv", "small_paper_trades_review.csv"):
        p = sdir / csv_name
        if not p.is_file():
            continue
        for row in mod.load_structural_trades(p):
            sym = str(row.get("symbol") or "")
            ent = str(row.get("entry_time") or "")
            m = mod._float(row.get("mfe_pct"))
            px = mod._float(row.get("entry_price"))
            if sym and ent:
                if m is not None:
                    mfe_map[(sym, ent)] = float(m)
                if px is not None and px > 0:
                    px_map[(sym, ent)] = float(px)
    return mfe_map, px_map


def _enrich_accept_features(
    mod: Any,
    session_rel: str,
    trades: list[dict[str, Any]],
    book_cache: dict[tuple[str, Any], list[Any]],
    ring_cache: dict[tuple[str, str], list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    sdir = mod.BASE / session_rel
    accept_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in mod._load_events(sdir):
        if ev.get("event_type") != "accepted":
            continue
        accept_by_key[(str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))] = ev

    out: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol") or "")
        entry_time = str(t.get("entry_time") or "")
        entry_ts = mod._parse_ts(entry_time)
        entry_day = mod._day_stamp(session_rel, entry_time)
        acc = accept_by_key.get((sym, entry_time), {})

        tv = mod._float(acc.get("trading_value"))
        vwap_dev = mod._float(acc.get("entry_vwap_dev_pct"))
        imb: Optional[float] = None
        entry_px = mod._float(t.get("entry_price")) or mod._float(acc.get("current_price"))
        push_dir = mod._push_dir_for_day(entry_day) or mod._push_dir(session_rel)
        cache_key = (entry_day, sym)
        if push_dir and cache_key not in book_cache:
            book_cache[cache_key] = mod._load_entry_series(push_dir, sym)
        ticks = book_cache.get(cache_key, [])
        snap = mod._lookup_at(ticks, entry_ts)
        if snap:
            imb = snap[1]
            if tv is None:
                tv = snap[2]
            if entry_px is None:
                entry_px = snap[4]
            if vwap_dev is None and entry_px and snap[3]:
                vwap_dev = mod._vwap_dev(float(entry_px), snap[3])

        rise_5 = mod._float(acc.get("entry_rise_5min_pct"))
        rise_10 = mod._float(acc.get("entry_rise_10min_pct"))
        if (rise_5 is None or rise_10 is None) and push_dir and entry_px:
            full_ring = _get_price_ring(mod, ring_cache, push_dir, sym)
            ring = _slice_ring_to_entry(full_ring, entry_ts)
            r5, r10 = _compute_rise_fields(ring, entry_ts, float(entry_px))
            if rise_5 is None:
                rise_5 = r5
            if rise_10 is None:
                rise_10 = r10

        row = {
            **t,
            "session_id": session_rel,
            "day_stamp": entry_day,
            "split": "in_sample" if session_rel in mod.IN_SAMPLE else "oos",
            "mfe_pct": mod._float(t.get("mfe_pct")),
            "trading_value": tv,
            "turnover_proxy": mod._float(acc.get("turnover_proxy")),
            "entry_vwap_dev_pct": vwap_dev,
            "order_book_imbalance": imb,
            "continuation_quality_score": mod._float(acc.get("continuation_quality_score")),
            "daytrade_suitability_score": mod._float(acc.get("daytrade_suitability_score")),
            "momentum_continuation_score": mod._float(acc.get("momentum_continuation_score")),
            "max_continuation_duration": mod._float(acc.get("max_continuation_duration")),
            "entry_rise_5min_pct": rise_5,
            "entry_rise_10min_pct": rise_10,
        }
        out.append(row)
    return out


def _build_all_trades(mod: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    p71 = mod._load_phase71()
    book_cache: dict[tuple[str, Any], list[Any]] = {}
    ring_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}
    all_rows: list[dict[str, Any]] = []

    mfe_sources = {"csv": 0, "replay_v1_peak_pnl": 0}
    for i, session_rel in enumerate(mod.ALL_SESSIONS, 1):
        trades, source = mod._load_session_trades(session_rel, p71)
        if not trades:
            print(f"  [{i}/{len(mod.ALL_SESSIONS)}] skip {session_rel} (no trades)", flush=True)
            continue
        # attach mfe from csv when missing
        sdir = mod.BASE / session_rel
        mfe_map, px_map = _csv_mfe_maps(mod, sdir)
        replay_mfe = _replay_mfe_map(p71, sdir) if sdir.is_dir() else {}
        for t in trades:
            key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            if key in mfe_map:
                t["mfe_pct"] = mfe_map[key]
                t["_mfe_source"] = "csv"
            elif key in replay_mfe:
                t["mfe_pct"] = replay_mfe[key]
                t["_mfe_source"] = "replay_v1_peak_pnl"
            if key in px_map:
                t["entry_price"] = px_map[key]
        enriched = _enrich_accept_features(mod, session_rel, trades, book_cache, ring_cache)
        seen: set[tuple[str, str]] = set()
        session_n = 0
        for r in enriched:
            key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            if r.get("mfe_pct") is None:
                continue
            src = str(r.pop("_mfe_source", "") or "")
            if src == "csv":
                mfe_sources["csv"] += 1
            elif src == "replay_v1_peak_pnl":
                mfe_sources["replay_v1_peak_pnl"] += 1
            all_rows.append(r)
            session_n += 1
        print(f"  [{i}/{len(mod.ALL_SESSIONS)}] {session_rel} n={session_n} src={source}", flush=True)
    return all_rows, mfe_sources


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mod = _load_phase213c_module()
    print("loading IS+OOS trades...", flush=True)
    rows, mfe_sources = _build_all_trades(mod)

    group_a = [r for r in rows if float(r["mfe_pct"]) >= MFE_A_MIN]
    group_b = [r for r in rows if float(r["mfe_pct"]) < MFE_B_MAX]
    middle = [r for r in rows if MFE_B_MAX <= float(r["mfe_pct"]) < MFE_A_MIN]

    comparisons = [_compare_feature(label, key, group_a, group_b) for label, key in FEATURES]
    ranked = sorted(
        [c for c in comparisons if c.get("cohen_d") is not None],
        key=lambda x: abs(float(x["cohen_d"])),
        reverse=True,
    )

    report = {
        "phase": 216,
        "mode": "high_mfe_winner_attribution_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "population": {
            "session_scope": "IS 11 + OOS 9 (all accepted structural trades with mfe_pct)",
            "total_trades": len(rows),
            "in_sample_count": sum(1 for r in rows if r.get("split") == "in_sample"),
            "oos_count": sum(1 for r in rows if r.get("split") == "oos"),
            "mfe_source_counts": mfe_sources,
            "mfe_pct_unit": "percent points (e.g. 3.0 = 3%)",
        },
        "groups": {
            "A_high_mfe": {
                "definition": f"MFE >= {MFE_A_MIN}%",
                "trade_count": len(group_a),
                "share_of_all_pct": round(100.0 * len(group_a) / max(1, len(rows)), 2),
                "avg_mfe_pct": _mean(_vals(group_a, "mfe_pct")),
                "avg_realized_pnl_pct": _mean(_vals(group_a, "pnl_pct")),
            },
            "B_low_mfe": {
                "definition": f"MFE < {MFE_B_MAX}%",
                "trade_count": len(group_b),
                "share_of_all_pct": round(100.0 * len(group_b) / max(1, len(rows)), 2),
                "avg_mfe_pct": _mean(_vals(group_b, "mfe_pct")),
                "avg_realized_pnl_pct": _mean(_vals(group_b, "pnl_pct")),
            },
            "middle_excluded": {
                "definition": f"{MFE_B_MAX}% <= MFE < {MFE_A_MIN}%",
                "trade_count": len(middle),
            },
        },
        "feature_comparisons": comparisons,
        "top_predictive_features_by_abs_cohen_d": ranked[:10],
        "interpretation_hints": {
            "positive_cohen_d": "A (high MFE) group has higher feature values than B",
            "negative_cohen_d": "B (low MFE) group has higher feature values than A",
        },
        "notes": [
            "Goal: identify characteristics of big runners, not loss reduction.",
            "MFE from structural/review CSV when present; else v1 replay peak tick pnl.",
            "Entry features at accept; order_book_imbalance from push_jsonl.",
            "rise_5min/10min from accept log or push price ring (no research import).",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} total={len(rows)} A={len(group_a)} B={len(group_b)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
