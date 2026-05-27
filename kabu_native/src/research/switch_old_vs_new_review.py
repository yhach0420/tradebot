"""
Phase 133: Cross-symbol switch review — old hold vs new entry after exit (review only).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.mfe_mae_exit_review import (
    as_float,
    best_pnl_in_window,
    build_price_timeline_from_events_csv,
    discover_sessions,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    price_at_horizon,
    session_end_ts_from_trades,
)
from research.range_hold_exit_review import _breakdown_on_tick

SWITCH_EXIT_REASONS = frozenset(
    {
        "overlap_replaced_review",
        "momentum_fade_exit",
        "price_momentum_fade_exit",
    }
)

HORIZONS_SEC = (30, 60, 180, 300)
MAX_PAIR_SEC = 300.0
PNL_EPS = 0.01
DRAWDOWN_SMALL_PCT = 0.15
GIVEBACK_SMALL_FRAC = 0.15


def _find_next_cross_symbol_entry(
    trades: Sequence[Mapping[str, Any]],
    *,
    old_symbol: str,
    old_close_ts: float,
    max_pair_sec: float = MAX_PAIR_SEC,
) -> Optional[dict[str, Any]]:
    best: Optional[dict[str, Any]] = None
    best_delta = 1e18
    for t in trades:
        sym = str(t.get("symbol") or "")
        if sym == old_symbol:
            continue
        ent_ts = parse_ts(str(t.get("entry_time") or ""))
        if ent_ts < old_close_ts - 1.0:
            continue
        gap = ent_ts - old_close_ts
        if gap < 0 or gap > max_pair_sec:
            continue
        if best is None or gap < best_delta:
            best = dict(t)
            best_delta = gap
    return best


def _old_pre_exit_flags(
    trade: Mapping[str, Any],
    timeline: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    close_ts: float,
    entry_price: float,
    close_price: float,
) -> dict[str, bool]:
    mfe = as_float(trade.get("mfe_pct")) or 0.0
    mae = as_float(trade.get("mae_pct")) or 0.0
    exit_pnl = as_float(trade.get("realized_pnl_pct")) or pnl_pct(entry_price, close_price)
    pre_prices = [px for ts, px in timeline if entry_ts <= ts <= close_ts]
    recent_low = min(pre_prices) if pre_prices else close_price
    breakdown = _breakdown_on_tick(
        px=close_price,
        pnl=exit_pnl,
        mom=None,
        fade_momentum=None,
        fade_price=close_price,
        recent_low=recent_low,
        peak_pnl=max(mfe, exit_pnl),
        post_low=close_price,
        prev_post_low=close_price,
        new_high_since_fade=False,
    )
    giveback_small = mfe <= PNL_EPS or (mfe > 0 and exit_pnl >= mfe * (1.0 - GIVEBACK_SMALL_FRAC))
    drawdown_small = mae >= -DRAWDOWN_SMALL_PCT
    range_hold = (not breakdown) and giveback_small and drawdown_small
    return {
        "old_breakdown_before_exit": breakdown,
        "old_range_hold_before_exit": range_hold,
        "old_drawdown_small": drawdown_small,
        "old_mfe_giveback_small": giveback_small,
    }


def _path_after_switch(
    timeline: Sequence[tuple[float, float]],
    *,
    entry_price: float,
    switch_ts: float,
    session_end_ts: float,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS_SEC:
        out[f"pnl_{h}s"] = price_at_horizon(
            timeline,
            base_ts=switch_ts,
            entry_price=entry_price,
            horizon_sec=float(h),
            session_end_ts=session_end_ts,
        )
        out[f"best_{h}s"] = best_pnl_in_window(
            timeline,
            base_ts=switch_ts,
            entry_price=entry_price,
            window_sec=float(h),
            session_end_ts=session_end_ts,
        )
    out["pnl_session_end"] = price_at_horizon(
        timeline,
        base_ts=switch_ts,
        entry_price=entry_price,
        horizon_sec=max(1.0, session_end_ts - switch_ts),
        session_end_ts=session_end_ts,
    )
    out["best_session_end"] = best_pnl_in_window(
        timeline,
        base_ts=switch_ts,
        entry_price=entry_price,
        window_sec=max(1.0, session_end_ts - switch_ts),
        session_end_ts=session_end_ts,
    )
    return out


def _classify_switch(
    old_pnl: Optional[float],
    new_pnl: Optional[float],
) -> str:
    if old_pnl is None or new_pnl is None:
        return "insufficient_data"
    if old_pnl > PNL_EPS and new_pnl > PNL_EPS:
        return "both_good"
    if old_pnl < -PNL_EPS and new_pnl < -PNL_EPS:
        return "both_bad"
    if new_pnl > old_pnl + PNL_EPS:
        return "switch_correct"
    if old_pnl > new_pnl + PNL_EPS:
        return "switch_wrong"
    return "both_good" if old_pnl >= 0 and new_pnl >= 0 else "both_bad"


def extract_switch_pairs(session_dir: Path) -> list[dict[str, Any]]:
    session_dir = Path(session_dir)
    trades = load_structural_trades(session_dir / "structural_trades.csv")
    if not trades:
        return []

    session_id = (
        str(session_dir.relative_to(session_dir.parent.parent))
        if session_dir.parent.parent
        else session_dir.name
    )
    end_ts = session_end_ts_from_trades(trades)
    events_csv = session_dir / "small_paper_events.csv"
    symbols = {str(t.get("symbol") or "") for t in trades}
    tl_map = build_price_timeline_from_events_csv(events_csv, symbols)

    pairs: list[dict[str, Any]] = []
    for old in trades:
        reason = str(old.get("close_reason") or "")
        if reason not in SWITCH_EXIT_REASONS:
            continue
        old_sym = str(old.get("symbol") or "")
        old_close_ts = parse_ts(str(old.get("close_time") or ""))
        old_entry_ts = parse_ts(str(old.get("entry_time") or ""))
        old_entry_px = as_float(old.get("entry_price")) or 0.0
        old_close_px = as_float(old.get("close_price")) or old_entry_px
        if old_close_ts <= 0 or old_entry_px <= 0:
            continue

        new = _find_next_cross_symbol_entry(trades, old_symbol=old_sym, old_close_ts=old_close_ts)
        if not new:
            continue

        new_sym = str(new.get("symbol") or "")
        new_entry_ts = parse_ts(str(new.get("entry_time") or ""))
        new_entry_px = as_float(new.get("entry_price")) or 0.0
        switch_ts = max(old_close_ts, new_entry_ts)
        gap_sec = round(new_entry_ts - old_close_ts, 1)

        old_tl = tl_map.get(old_sym, [])
        new_tl = tl_map.get(new_sym, [])
        if len(old_tl) < 3 or len(new_tl) < 3:
            continue

        pre_flags = _old_pre_exit_flags(
            old, old_tl, entry_ts=old_entry_ts, close_ts=old_close_ts,
            entry_price=old_entry_px, close_price=old_close_px,
        )
        old_path = _path_after_switch(old_tl, entry_price=old_entry_px, switch_ts=switch_ts, session_end_ts=end_ts)
        new_path = _path_after_switch(new_tl, entry_price=new_entry_px, switch_ts=switch_ts, session_end_ts=end_ts)

        old_pnl_se = old_path.get("pnl_session_end")
        new_pnl_se = new_path.get("pnl_session_end")
        old_best_se = old_path.get("best_session_end")
        new_best_se = new_path.get("best_session_end")
        delta_se = (
            round(float(new_pnl_se) - float(old_pnl_se), 4)
            if old_pnl_se is not None and new_pnl_se is not None
            else None
        )

        switch_class = _classify_switch(old_pnl_se, new_pnl_se)
        old_reaccel = (
            old_best_se is not None
            and old_pnl_se is not None
            and float(old_best_se) > float(old_pnl_se) + PNL_EPS
            and float(old_best_se) > PNL_EPS
        )

        row: dict[str, Any] = {
            "session_id": session_id,
            "old_symbol": old_sym,
            "new_symbol": new_sym,
            "old_exit_reason": reason,
            "old_entry_time": old.get("entry_time"),
            "old_close_time": old.get("close_time"),
            "new_entry_time": new.get("entry_time"),
            "switch_gap_sec": gap_sec,
            "switch_time": old.get("close_time"),
            "old_pnl_at_exit": as_float(old.get("realized_pnl_pct")),
            "old_mfe_pct": as_float(old.get("mfe_pct")),
            "old_mae_pct": as_float(old.get("mae_pct")),
            "new_quality": as_float(new.get("continuation_quality_score")),
            "switch_classification": switch_class,
            "old_pnl_after_switch": old_pnl_se,
            "new_pnl_after_switch": new_pnl_se,
            "old_best_pnl": old_best_se,
            "new_best_pnl": new_best_se,
            "delta_new_minus_old": delta_se,
            "old_reaccelerated_after_exit": old_reaccel,
            "new_failed": new_pnl_se is not None and float(new_pnl_se) < -PNL_EPS,
            "both_positive": old_pnl_se is not None and new_pnl_se is not None and float(old_pnl_se) > 0 and float(new_pnl_se) > 0,
            "both_negative": old_pnl_se is not None and new_pnl_se is not None and float(old_pnl_se) < 0 and float(new_pnl_se) < 0,
            **pre_flags,
        }
        for h in HORIZONS_SEC:
            row[f"old_pnl_{h}s"] = old_path.get(f"pnl_{h}s")
            row[f"new_pnl_{h}s"] = new_path.get(f"pnl_{h}s")
            row[f"old_best_{h}s"] = old_path.get(f"best_{h}s")
            row[f"new_best_{h}s"] = new_path.get(f"best_{h}s")
            o = old_path.get(f"pnl_{h}s")
            n = new_path.get(f"pnl_{h}s")
            row[f"delta_{h}s"] = round(float(n) - float(o), 4) if o is not None and n is not None else None

        row["wrong_switch_candidate"] = (
            switch_class == "switch_wrong"
            and (
                pre_flags.get("old_range_hold_before_exit")
                or (pre_flags.get("old_mfe_giveback_small") and not pre_flags.get("old_breakdown_before_exit"))
            )
            and (old_reaccel or (old_best_se is not None and old_pnl_se is not None and float(old_best_se) > float(old_pnl_se)))
        )
        pairs.append(row)

    return pairs


def _aggregate(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(pairs)
    if n == 0:
        return {"switch_count": 0}

    classes = Counter(str(p.get("switch_classification") or "") for p in pairs)
    deltas = [float(p["delta_new_minus_old"]) for p in pairs if p.get("delta_new_minus_old") is not None]
    by_reason: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_session: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for p in pairs:
        by_reason[str(p.get("old_exit_reason") or "")].append(p)
        by_session[str(p.get("session_id") or "")].append(p)

    def _rate(cls: str) -> Optional[float]:
        return round(classes.get(cls, 0) / n, 4) if n else None

    by_reason_rows = []
    for reason, rows in sorted(by_reason.items()):
        d = [float(r["delta_new_minus_old"]) for r in rows if r.get("delta_new_minus_old") is not None]
        c = Counter(str(r.get("switch_classification") or "") for r in rows)
        by_reason_rows.append(
            {
                "old_exit_reason": reason,
                "count": len(rows),
                "correct_rate": round(c.get("switch_correct", 0) / len(rows), 4),
                "wrong_rate": round(c.get("switch_wrong", 0) / len(rows), 4),
                "avg_delta": round(statistics.mean(d), 4) if d else None,
                "total_delta": round(sum(d), 4) if d else None,
            }
        )

    return {
        "switch_count": n,
        "correct_rate": _rate("switch_correct"),
        "wrong_rate": _rate("switch_wrong"),
        "both_good_rate": _rate("both_good"),
        "both_bad_rate": _rate("both_bad"),
        "insufficient_data_rate": _rate("insufficient_data"),
        "avg_delta_new_minus_old": round(statistics.mean(deltas), 4) if deltas else None,
        "total_delta_new_minus_old": round(sum(deltas), 4) if deltas else None,
        "wrong_switch_candidate_count": sum(1 for p in pairs if p.get("wrong_switch_candidate")),
        "by_exit_reason": by_reason_rows,
        "by_session": {
            sid: {
                "count": len(rows),
                "wrong_rate": round(
                    sum(1 for r in rows if r.get("switch_classification") == "switch_wrong") / len(rows), 4
                ),
                "total_delta": round(
                    sum(float(r["delta_new_minus_old"]) for r in rows if r.get("delta_new_minus_old") is not None), 4
                ),
            }
            for sid, rows in by_session.items()
        },
    }


def determine_verdict(agg: Mapping[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = int(agg.get("switch_count") or 0)
    if n < 20:
        return "insufficient_switch_data", notes + [f"switch_count={n}"]

    wrong = float(agg.get("wrong_rate") or 0)
    correct = float(agg.get("correct_rate") or 0)
    total_delta = float(agg.get("total_delta_new_minus_old") or 0)
    wrong_cand = int(agg.get("wrong_switch_candidate_count") or 0)
    notes.append(
        f"n={n} correct={correct:.1%} wrong={wrong:.1%} total_delta={total_delta:.4f} "
        f"wrong_range_hold={wrong_cand}"
    )

    if wrong >= 0.45 and total_delta < 0:
        return "switch_logic_hurting_pnl", notes
    if wrong >= 0.40 or wrong_cand >= n * 0.25:
        return "need_switch_priority_model", notes
    if correct >= 0.40 and total_delta > 0:
        return "switch_logic_reasonable", notes
    if total_delta > 0 and wrong < 0.35:
        return "switch_logic_reasonable", notes
    return "need_switch_priority_model", notes


def analyze_switch_old_vs_new(session_dirs: Sequence[Path]) -> dict[str, Any]:
    all_pairs: list[dict[str, Any]] = []
    for sdir in session_dirs:
        all_pairs.extend(extract_switch_pairs(sdir))

    agg = _aggregate(all_pairs)
    verdict, notes = determine_verdict(agg)
    wrong_examples = [p for p in all_pairs if p.get("wrong_switch_candidate")][:50]
    if len(wrong_examples) < 20:
        wrong_examples = sorted(
            [p for p in all_pairs if p.get("switch_classification") == "switch_wrong"],
            key=lambda r: float(r.get("delta_new_minus_old") or 0),
        )[:50]

    summary_rows = [
        {
            "metric": "switch_count",
            "value": agg.get("switch_count"),
        },
        {
            "metric": "correct_rate",
            "value": agg.get("correct_rate"),
        },
        {
            "metric": "wrong_rate",
            "value": agg.get("wrong_rate"),
        },
        {
            "metric": "both_good_rate",
            "value": agg.get("both_good_rate"),
        },
        {
            "metric": "both_bad_rate",
            "value": agg.get("both_bad_rate"),
        },
        {
            "metric": "avg_delta_new_minus_old",
            "value": agg.get("avg_delta_new_minus_old"),
        },
        {
            "metric": "total_delta_new_minus_old",
            "value": agg.get("total_delta_new_minus_old"),
        },
        {
            "metric": "wrong_switch_candidate_count",
            "value": agg.get("wrong_switch_candidate_count"),
        },
    ]
    summary_rows.extend(agg.get("by_exit_reason") or [])

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "aggregate": agg,
        "switch_pairs": all_pairs,
        "summary_rows": summary_rows,
        "wrong_switch_examples": wrong_examples,
    }
