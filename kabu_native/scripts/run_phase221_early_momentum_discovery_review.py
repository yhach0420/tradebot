#!/usr/bin/env python3
"""
Phase221: Early momentum discovery review (review only).

Compare top/bottom 20% pnl cohorts on entry-time early-rise features.
Goal: identify characteristics of stocks at the start of upward moves.
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
OUT = REPO / "kabu_native/results/reports/phase221_early_momentum_discovery_review.json"

FEATURES: tuple[tuple[str, str], ...] = (
    ("continuation_duration", "max_continuation_duration"),
    ("momentum_continuation", "momentum_continuation_score"),
    ("rise_1min", "entry_rise_1min_pct"),
    ("rise_3min", "entry_rise_3min_pct"),
    ("rise_5min", "entry_rise_5min_pct"),
    ("rolling_mfe", "rolling_mfe_pct"),
    ("rolling_mae", "rolling_mae_pct"),
    ("entry_vwap_dev", "entry_vwap_dev_pct"),
    ("board_imbalance", "entry_order_book_imbalance"),
    ("high_break_count", "high_break_count"),
)


def _load_phase217() -> Any:
    path = REPO / "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py"
    name = "phase217_loader_p221"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _vals(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [v for r in rows if (v := _float(r.get(key))) is not None]


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 6) if xs else None


def _median(xs: list[float]) -> Optional[float]:
    return round(statistics.median(xs), 6) if xs else None


def _cohen_d(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    pooled = math.sqrt(((len(a) - 1) * sa * sa + (len(b) - 1) * sb * sb) / (len(a) + len(b) - 2))
    if pooled <= 1e-12:
        return None
    return round((ma - mb) / pooled, 4)


def _compare(label: str, key: str, a: list[dict], b: list[dict]) -> dict[str, Any]:
    av, bv = _vals(a, key), _vals(b, key)
    ma, mb = _mean(av), _mean(bv)
    return {
        "feature": label,
        "field": key,
        "A_top20_count": len(av),
        "B_bottom20_count": len(bv),
        "A_mean": ma,
        "B_mean": mb,
        "A_median": _median(av),
        "B_median": _median(bv),
        "delta_A_minus_B": round(ma - mb, 6) if ma is not None and mb is not None else None,
        "cohen_d": _cohen_d(av, bv),
        "A_higher": ma is not None and mb is not None and ma > mb,
    }


def _price_before(ring: list[tuple[float, float]], ts: float, lookback: float) -> Optional[float]:
    target = ts - lookback
    found: Optional[float] = None
    for t, px in ring:
        if t <= target:
            found = px
        elif t > ts:
            break
    return found


def _rise_pct(entry_px: float, prior: Optional[float]) -> Optional[float]:
    if prior is None or prior <= 0 or entry_px <= 0:
        return None
    return round((entry_px - prior) / prior * 100.0, 4)


def _high_break_count(ring: list[tuple[float, float]], entry_ts: float, lookback: float = 600.0) -> int:
    window = [(t, px) for t, px in ring if entry_ts - lookback <= t <= entry_ts]
    if len(window) < 2:
        return 0
    running = window[0][1]
    count = 0
    for _, px in window[1:]:
        if px > running * 1.0001:
            count += 1
            running = px
    return count


def _get_price_ring(
    mod: Any,
    cache: dict[tuple[str, str], list[tuple[float, float]]],
    push_dir: Path,
    symbol: str,
) -> list[tuple[float, float]]:
    from small_paper.extended_entry_shadow import append_price_tick

    key = (str(push_dir), symbol.replace(".T", ""))
    if key in cache:
        return cache[key]
    sym = symbol.replace(".T", "")
    path = None
    for name in (f"{symbol}.jsonl", f"{sym}.jsonl"):
        p = push_dir / name
        if p.is_file():
            path = p
            break
    if path is None:
        cache[key] = []
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
            px = mod._float((rec.get("payload") or {}).get("CurrentPrice"))
            if px and px > 0:
                append_price_tick(ring, ts=ts, px=float(px))
    cache[key] = ring
    return ring


def _augment_early_features(mod: Any, rows: list[dict[str, Any]]) -> None:
    ring_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}
    accept_cache: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

    for r in rows:
        session_rel = str(r.get("session_id") or "")
        if session_rel and session_rel not in accept_cache:
            sdir = mod.BASE / session_rel
            acc: dict[tuple[str, str], dict[str, Any]] = {}
            for ev in mod._load_events(sdir):
                if ev.get("event_type") == "accepted":
                    acc[(str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))] = ev
            accept_cache[session_rel] = acc

        key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
        acc = accept_cache.get(session_rel, {}).get(key, {})

        if r.get("max_continuation_duration") is None:
            dur = mod._float(acc.get("max_continuation_duration"))
            if dur is not None:
                r["max_continuation_duration"] = dur
        if r.get("rolling_mfe_pct") is None:
            rm = mod._float(acc.get("rolling_mfe_pct"))
            if rm is not None:
                r["rolling_mfe_pct"] = rm
        if r.get("rolling_mae_pct") is None:
            ra = mod._float(acc.get("rolling_mae_pct"))
            if ra is not None:
                r["rolling_mae_pct"] = ra

        entry_px = _float(r.get("current_price"))
        entry_time = str(r.get("entry_time") or "")
        entry_ts = mod._parse_ts(entry_time)
        entry_day = str(r.get("day_stamp") or "")
        push_dir = mod._push_dir_for_day(entry_day) or mod._push_dir(session_rel)

        r5 = _float(r.get("entry_rise_5min_pct")) or _float(acc.get("entry_rise_5min_pct"))
        if entry_px and push_dir:
            ring_full = _get_price_ring(mod, ring_cache, push_dir, str(r.get("symbol") or ""))
            ring = ring_full[: bisect_right([t for t, _ in ring_full], entry_ts)]
            if r.get("entry_rise_1min_pct") is None:
                r["entry_rise_1min_pct"] = _rise_pct(entry_px, _price_before(ring, entry_ts, 60))
            if r.get("entry_rise_3min_pct") is None:
                r["entry_rise_3min_pct"] = _rise_pct(entry_px, _price_before(ring, entry_ts, 180))
            if r5 is None:
                r5 = _rise_pct(entry_px, _price_before(ring, entry_ts, 300))
            r["entry_rise_5min_pct"] = r5
            if r.get("high_break_count") is None:
                r["high_break_count"] = _high_break_count(ring, entry_ts, 600.0)

        hb = acc.get("entry_high_break_recent")
        if hb is not None:
            r["entry_high_break_recent"] = str(hb).lower() in ("true", "1", "yes")


def _quantile_rows(rows: list[dict[str, Any]], q_low: float, q_high: float) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda r: float(r.get("pnl_pct") or 0))
    n = len(ranked)
    i0 = max(0, int(n * q_low))
    i1 = min(n, max(i0 + 1, int(math.ceil(n * q_high))))
    return ranked[i0:i1]


def _early_momentum_signals(comparisons: list[dict[str, Any]]) -> list[str]:
    """Heuristic: early rise = moderate rise + momentum, not fully extended."""
    hints: list[str] = []
    by_name = {c["feature"]: c for c in comparisons}
    r5 = by_name.get("rise_5min")
    mom = by_name.get("momentum_continuation")
    dur = by_name.get("continuation_duration")
    rmfe = by_name.get("rolling_mfe")
    hb = by_name.get("high_break_count")
    if r5 and r5.get("cohen_d") is not None:
        if float(r5["cohen_d"]) > 0.15:
            hints.append("Top pnl cohort entered after larger 5min rise — less 'early' by price path.")
        elif float(r5["cohen_d"]) < -0.15:
            hints.append("Top pnl cohort has lower 5min rise — consistent with earlier-stage entry.")
    if mom and mom.get("cohen_d") is not None and float(mom["cohen_d"]) > 0.15:
        hints.append("Higher momentum_continuation at entry aligns with top pnl cohort.")
    if dur and dur.get("cohen_d") is not None and float(dur["cohen_d"]) < -0.15:
        hints.append("Shorter continuation_duration at entry suggests fresher continuation leg.")
    if rmfe and rmfe.get("cohen_d") is not None and float(rmfe["cohen_d"]) < -0.15:
        hints.append("Lower rolling_mfe at entry — less extended before fill.")
    if hb and hb.get("cohen_d") is not None and float(hb["cohen_d"]) > 0.15:
        hints.append("More high-break updates before entry on top pnl cohort.")
    return hints


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_phase217()
    mod = p217._load_phase213c_module()
    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("augmenting early momentum features...", flush=True)
    _augment_early_features(mod, rows)

    top20 = _quantile_rows(rows, 0.80, 1.0)
    bottom20 = _quantile_rows(rows, 0.0, 0.20)

    comparisons = [_compare(lbl, key, top20, bottom20) for lbl, key in FEATURES]
    ranked = sorted(
        [c for c in comparisons if c.get("cohen_d") is not None],
        key=lambda x: abs(float(x["cohen_d"])),
        reverse=True,
    )

    def _bool_rate(rs: list[dict], key: str) -> Optional[float]:
        xs = [r for r in rs if r.get(key) is not None]
        if not xs:
            return None
        return round(sum(1 for r in xs if str(r.get(key)).lower() in ("true", "1", "yes")) / len(xs), 4)

    pnls_top = [float(r.get("pnl_pct") or 0) for r in top20]
    pnls_bot = [float(r.get("pnl_pct") or 0) for r in bottom20]

    report = {
        "phase": 221,
        "mode": "early_momentum_discovery_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "goal": "Identify entry features of early upward moves (not winner labeling per se).",
        "population": {
            "total_trades": len(rows),
            "A_top20pct_by_pnl": {
                "trade_count": len(top20),
                "pnl_range": {
                    "min": round(pnls_top[0], 4) if pnls_top else None,
                    "max": round(pnls_top[-1], 4) if pnls_top else None,
                },
                "avg_pnl_pct": round(sum(pnls_top) / len(pnls_top), 4) if pnls_top else None,
                "avg_mfe_pct": _mean(_vals(top20, "mfe_pct")),
            },
            "B_bottom20pct_by_pnl": {
                "trade_count": len(bottom20),
                "pnl_range": {
                    "min": round(pnls_bot[0], 4) if pnls_bot else None,
                    "max": round(pnls_bot[-1], 4) if pnls_bot else None,
                },
                "avg_pnl_pct": round(sum(pnls_bot) / len(pnls_bot), 4) if pnls_bot else None,
                "avg_mfe_pct": _mean(_vals(bottom20, "mfe_pct")),
            },
        },
        "feature_comparisons_A_top20_vs_B_bottom20": comparisons,
        "top_discriminators_by_abs_cohen_d": ranked[:10],
        "entry_high_break_recent_rate": {
            "A_top20": _bool_rate(top20, "entry_high_break_recent"),
            "B_bottom20": _bool_rate(bottom20, "entry_high_break_recent"),
        },
        "early_momentum_interpretation": _early_momentum_signals(comparisons),
        "feature_coverage": {
            lbl: round(sum(1 for r in rows if r.get(fld) is not None) / max(1, len(rows)), 4)
            for lbl, fld in FEATURES
        },
        "notes": [
            "A/B = top/bottom 20% of all trades by realized pnl_pct (includes losers in bottom).",
            "rise_1/3/5min from push price ring; high_break_count = new highs in 10min pre-entry window.",
            "rolling_mfe_pct at accept (session-local ratio as logged).",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} top20={len(top20)} bottom20={len(bottom20)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
