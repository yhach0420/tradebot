"""
Phase 125: Post-fade reacceleration detection review (read-only).
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_extension_conditions import build_fade_cluster_rows
from research.mfe_mae_exit_review import (
    as_float,
    build_price_timeline_from_events_csv,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    session_end_ts_from_trades,
)

HORIZONS_SEC = (30, 60, 120)
PRIMARY_HORIZON = 60
REACCEL_EPS = 0.01


def _price_at_ts(
    timeline: Sequence[tuple[float, float]],
    target_ts: float,
    *,
    min_ts: float,
) -> Optional[float]:
    chosen: Optional[float] = None
    for ts, px in timeline:
        if ts < min_ts:
            continue
        if ts >= target_ts:
            return px
        chosen = px
    return chosen


def _momentum_at_ts(
    sym_events: Sequence[tuple[float, dict[str, Any]]],
    target_ts: float,
    *,
    min_ts: float,
    max_delta: float = 20.0,
) -> Optional[float]:
    best: Optional[float] = None
    best_d = 1e18
    for ts, row in sym_events:
        if ts < min_ts:
            continue
        d = abs(ts - target_ts)
        if d <= max_delta and d < best_d:
            best_d = d
            best = as_float(row.get("momentum_continuation_score"))
    return best


def _post_fade_window_stats(
    timeline: Sequence[tuple[float, float]],
    *,
    entry_price: float,
    close_ts: float,
    fade_price: float,
    mfe_at_exit: float,
    horizon_sec: float,
    session_end_ts: float,
) -> dict[str, Any]:
    end_ts = min(close_ts + horizon_sec, session_end_ts)
    points = [(ts, px) for ts, px in timeline if close_ts < ts <= end_ts]

    price_at_h = _price_at_ts(timeline, end_ts, min_ts=close_ts)
    pnl_at_h = pnl_pct(entry_price, price_at_h) if price_at_h else None

    running_high = fade_price
    new_high_count = 0
    time_to_new_high: Optional[float] = None
    best_px = fade_price
    best_pnl = pnl_pct(entry_price, fade_price)

    for ts, px in points:
        if px > running_high:
            if px > fade_price + 1e-9:
                new_high_count += 1
                if time_to_new_high is None:
                    time_to_new_high = round(ts - close_ts, 1)
            running_high = px
        if px > best_px:
            best_px = px
        p = pnl_pct(entry_price, px)
        if p > best_pnl:
            best_pnl = p

    new_high_after_fade = best_px > fade_price + 1e-9
    new_mfe_created = best_pnl > mfe_at_exit + REACCEL_EPS

    return {
        f"price_at_{int(horizon_sec)}s": price_at_h,
        f"pnl_at_{int(horizon_sec)}s": pnl_at_h,
        f"new_high_after_fade_{int(horizon_sec)}s": new_high_after_fade,
        f"new_high_count_{int(horizon_sec)}s": new_high_count,
        f"time_to_new_high_{int(horizon_sec)}s": time_to_new_high,
        f"best_pnl_after_fade_{int(horizon_sec)}s": round(best_pnl, 4),
        f"new_mfe_created_{int(horizon_sec)}s": new_mfe_created,
        f"price_above_fade_{int(horizon_sec)}s": (
            price_at_h is not None and price_at_h > fade_price + 1e-9
        ),
    }


def build_reacceleration_rows(session_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    from research.fade_extension_conditions import _build_sym_timelines

    base = build_fade_cluster_rows([Path(p) for p in session_dirs])
    rows: list[dict[str, Any]] = []

    session_meta: dict[str, dict[str, Any]] = {}
    for sdir in session_dirs:
        sdir = Path(sdir)
        sid = str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        trades_raw = load_structural_trades(sdir / "structural_trades.csv")
        symbols = {str(t.get("symbol") or "") for t in trades_raw}
        session_meta[sid] = {
            "end_ts": session_end_ts_from_trades(trades_raw),
            "tl_map": build_price_timeline_from_events_csv(sdir / "small_paper_events.csv", symbols),
            "sym_events": _build_sym_timelines(sdir / "small_paper_events.csv"),
            "trades_by_key": {
                (str(t.get("symbol")), str(t.get("entry_time"))): t for t in trades_raw
            },
        }

    for b in base:
        sid = str(b.get("session_id") or "")
        meta = session_meta.get(sid, {})
        sym = str(b.get("symbol") or "")
        tl = meta.get("tl_map", {}).get(sym, [])
        sym_ev = meta.get("sym_events", {}).get(sym, [])
        end_ts = float(meta.get("end_ts") or 0)
        trade = (meta.get("trades_by_key") or {}).get((sym, str(b.get("entry_time"))), {})

        entry_px = as_float(trade.get("entry_price")) or as_float(b.get("entry_price")) or 0.0
        fade_px = as_float(trade.get("close_price")) or entry_px
        close_ts = parse_ts(str(b.get("close_time") or ""))
        entry_ts = parse_ts(str(b.get("entry_time") or ""))
        mfe_at_exit = float(b.get("mfe_pct") or 0)

        fade_momentum = _momentum_at_ts(sym_ev, close_ts, min_ts=entry_ts)

        row: dict[str, Any] = {
            **b,
            "fade_price": fade_px,
            "entry_price": entry_px,
            "mfe_at_exit": mfe_at_exit,
            "fade_momentum": fade_momentum,
            "exit_pnl": b.get("pnl_at_exit"),
        }

        for h in HORIZONS_SEC:
            stats = _post_fade_window_stats(
                tl,
                entry_price=entry_px,
                close_ts=close_ts,
                fade_price=fade_px,
                mfe_at_exit=mfe_at_exit,
                horizon_sec=float(h),
                session_end_ts=end_ts,
            )
            row.update(stats)

            mom_h = _momentum_at_ts(sym_ev, close_ts + h, min_ts=close_ts)
            price_above = row.get(f"price_above_fade_{h}s")
            momentum_recovery = (
                bool(price_above)
                and mom_h is not None
                and fade_momentum is not None
                and mom_h > fade_momentum + 0.02
            )
            row[f"momentum_at_{h}s"] = mom_h
            row[f"momentum_recovery_{h}s"] = momentum_recovery
            row[f"reacceleration_{h}s"] = (
                bool(row.get(f"new_high_after_fade_{h}s"))
                and bool(price_above)
                and (momentum_recovery or bool(row.get(f"new_mfe_created_{h}s")))
            )

        row["reacceleration_cluster"] = (
            "reacceleration" if row.get(f"reacceleration_{PRIMARY_HORIZON}s") else "non_reacceleration"
        )
        rows.append(row)

    return rows


def _mean(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    vals = [as_float(r.get(key)) for r in rows]
    nums = [v for v in vals if v is not None]
    return round(statistics.mean(nums), 4) if nums else None


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    trues = sum(1 for r in rows if r.get(key) in (True, "True", 1))
    return round(trues / len(rows), 4)


def compare_clusters(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    h = PRIMARY_HORIZON
    key = f"reacceleration_{h}s"
    pos = [r for r in rows if r.get(key)]
    neg = [r for r in rows if not r.get(key)]

    def profile(name: str, grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "cluster": name,
            "count": len(grp),
            "avg_exit_pnl": _mean(grp, "exit_pnl"),
            "avg_mfe": _mean(grp, "mfe_pct"),
            "avg_hold_sec": _mean(grp, "hold_sec"),
            "avg_quality_score": _mean(grp, "quality_score"),
            "take_reached_rate": _rate(grp, "take_reached"),
            "overlap_replaced_rate": _rate(grp, "overlap_replaced"),
            f"avg_best_pnl_after_fade_{h}s": _mean(grp, f"best_pnl_after_fade_{h}s"),
            f"new_mfe_created_rate_{h}s": _rate(grp, f"new_mfe_created_{h}s"),
            "avg_hold60_delta": _mean(grp, "hold60_delta"),
            "total_hold60_delta": round(sum(float(r.get("hold60_delta") or 0) for r in grp), 4),
        }

    return [profile("reacceleration", pos), profile("non_reacceleration", neg)]


def _eval_rule(
    rows: Sequence[Mapping[str, Any]],
    rule: Mapping[str, Any],
    *,
    horizon: int = PRIMARY_HORIZON,
) -> dict[str, Any]:
    selected = [r for r in rows if _rule_match(r, rule)]
    reaccel = [r for r in rows if r.get(f"reacceleration_{horizon}s")]
    n = len(rows)
    sel_n = len(selected)
    tp = sum(1 for r in selected if r.get(f"reacceleration_{horizon}s"))
    prec = tp / sel_n if sel_n else None
    rec = tp / len(reaccel) if reaccel else None
    delta = round(sum(float(r.get("hold60_delta") or 0) for r in selected), 4)
    avg_best = _mean(selected, f"best_pnl_after_fade_{horizon}s")

    parts = []
    for k, v in rule.items():
        parts.append(f"{k}={v}")
    desc = " AND ".join(parts)

    return {
        "rule": rule,
        "description": desc,
        "horizon_sec": horizon,
        "selected_count": sel_n,
        "coverage": round(sel_n / n, 4) if n else None,
        "precision_reacceleration": round(prec, 4) if prec is not None else None,
        "recall_reacceleration": round(rec, 4) if rec is not None else None,
        "selected_total_hold60_delta": delta,
        "avg_best_pnl_after_fade": avg_best,
    }


def _rule_match(row: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    for k, v in rule.items():
        rv = row.get(k)
        if rv is None:
            return False
        if isinstance(v, bool):
            if bool(rv) != v:
                return False
        else:
            if bool(rv) != bool(v):
                return False
    return True


def explore_rules(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for h in HORIZONS_SEC:
        singles = [
            {f"new_high_after_fade_{h}s": True},
            {f"new_mfe_created_{h}s": True},
            {f"price_above_fade_{h}s": True},
            {f"momentum_recovery_{h}s": True},
            {f"reacceleration_{h}s": True},
        ]
        for rdef in singles:
            rules.append(_eval_rule(rows, rdef, horizon=h))

        rules.append(
            _eval_rule(
                rows,
                {f"new_high_after_fade_{h}s": True, f"momentum_recovery_{h}s": True},
                horizon=h,
            )
        )
        rules.append(
            _eval_rule(
                rows,
                {
                    f"new_high_after_fade_{h}s": True,
                    f"momentum_recovery_{h}s": True,
                    f"new_mfe_created_{h}s": True,
                },
                horizon=h,
            )
        )
        if h == 30:
            rules.append(
                _eval_rule(
                    rows,
                    {f"new_high_after_fade_30s": True, f"new_mfe_created_60s": True},
                    horizon=60,
                )
            )

    rules = [r for r in rules if r.get("selected_count", 0) >= 5]
    rules.sort(
        key=lambda r: (
            float(r.get("precision_reacceleration") or 0),
            float(r.get("selected_total_hold60_delta") or -1e9),
        ),
        reverse=True,
    )
    return rules


def build_price_paths_csv(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        path: dict[str, Any] = {
            "session_id": r.get("session_id"),
            "symbol": r.get("symbol"),
            "entry_time": r.get("entry_time"),
            "close_time": r.get("close_time"),
            "exit_reason": r.get("exit_reason"),
            "fade_price": r.get("fade_price"),
            "exit_pnl": r.get("exit_pnl"),
            "mfe_at_exit": r.get("mfe_at_exit"),
            "reacceleration_cluster": r.get("reacceleration_cluster"),
            "hold60_delta": r.get("hold60_delta"),
        }
        for h in HORIZONS_SEC:
            path[f"pnl_at_{h}s"] = r.get(f"pnl_at_{h}s")
            path[f"best_pnl_after_fade_{h}s"] = r.get(f"best_pnl_after_fade_{h}s")
            path[f"new_high_after_fade_{h}s"] = r.get(f"new_high_after_fade_{h}s")
            path[f"new_high_count_{h}s"] = r.get(f"new_high_count_{h}s")
            path[f"time_to_new_high_{h}s"] = r.get(f"time_to_new_high_{h}s")
            path[f"new_mfe_created_{h}s"] = r.get(f"new_mfe_created_{h}s")
            path[f"momentum_recovery_{h}s"] = r.get(f"momentum_recovery_{h}s")
            path[f"reacceleration_{h}s"] = r.get(f"reacceleration_{h}s")
        out.append(path)
    return out


def determine_verdict(
    rows: Sequence[Mapping[str, Any]],
    clusters: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = len(rows)
    h = PRIMARY_HORIZON
    reaccel_n = sum(1 for r in rows if r.get(f"reacceleration_{h}s"))
    reaccel_rate = reaccel_n / n if n else 0
    new_high_rate = sum(1 for r in rows if r.get(f"new_high_after_fade_{h}s")) / n if n else 0
    notes.append(
        f"n={n} reacceleration_{h}s={reaccel_n} rate={reaccel_rate:.1%} new_high_{h}s={new_high_rate:.1%}"
    )

    if not rules:
        return "fade_is_terminal", notes

    best = rules[0]
    prec = float(best.get("precision_reacceleration") or 0)
    rec = float(best.get("recall_reacceleration") or 0)
    notes.append(f"best_rule={best.get('description')} prec={prec:.3f} rec={rec:.3f}")

    reaccel_cluster = next((c for c in clusters if c.get("cluster") == "reacceleration"), {})
    non_cluster = next((c for c in clusters if c.get("cluster") == "non_reacceleration"), {})
    if reaccel_cluster and non_cluster:
        notes.append(
            f"reaccel_hold60_delta={reaccel_cluster.get('total_hold60_delta')} "
            f"non_reaccel={non_cluster.get('total_hold60_delta')}"
        )

    if reaccel_rate < 0.08:
        return "fade_is_terminal", notes

    if prec >= 0.55 and rec >= 0.35:
        return "reacceleration_detectable", notes

    if prec >= 0.4 or rec >= 0.25 or reaccel_rate >= 0.15:
        return "reacceleration_partially_detectable", notes

    if new_high_rate >= 0.2 and prec < 0.35:
        return "need_additional_intraday_features", notes

    return "fade_is_terminal", notes


def analyze_reacceleration(session_dirs: Sequence[Path]) -> dict[str, Any]:
    rows = build_reacceleration_rows(session_dirs)
    clusters = compare_clusters(rows)
    rules = explore_rules(rows)
    paths = build_price_paths_csv(rows)
    verdict, notes = determine_verdict(rows, clusters, rules)

    summary_by_horizon = {}
    for h in HORIZONS_SEC:
        summary_by_horizon[str(h)] = {
            "new_high_after_fade_rate": _rate(rows, f"new_high_after_fade_{h}s"),
            "new_mfe_created_rate": _rate(rows, f"new_mfe_created_{h}s"),
            "momentum_recovery_rate": _rate(rows, f"momentum_recovery_{h}s"),
            "reacceleration_rate": _rate(rows, f"reacceleration_{h}s"),
            "avg_best_pnl_after_fade": _mean(rows, f"best_pnl_after_fade_{h}s"),
        }

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_trade_count": len(rows),
        "horizon_summary": summary_by_horizon,
        "clusters": clusters,
        "rule_candidates": rules[:40],
        "price_paths": paths,
        "trade_rows": rows,
    }
