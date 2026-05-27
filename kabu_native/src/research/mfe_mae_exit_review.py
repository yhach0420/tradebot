"""
Phase 120: MFE/MAE vs exit capture review (read-only / what-if hypotheses).
"""

from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

POST_EXIT_HORIZONS_SEC = (30, 60, 180)
MFE_CAPTURE_LOW = 0.35
MFE_LARGE_PCT = 0.25
POST_EXIT_FOLLOWTHROUGH_PCT = 0.15
OVERLAP_COUNTERFACTUAL_PCT = 0.20
MOMENTUM_FADE_REBOUND_RATE = 0.40


def parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def as_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def pnl_pct(entry_price: float, px: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((px - entry_price) / entry_price * 100.0, 4)


def load_structural_trades(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(dict(row))
    return rows


def discover_sessions(
    small_paper_root: Path,
    *,
    max_sessions: int = 8,
) -> list[Path]:
    found: list[tuple[float, Path]] = []
    if not small_paper_root.is_dir():
        return []
    for trades_path in small_paper_root.glob("**/structural_trades.csv"):
        events_path = trades_path.parent / "structural_events.csv"
        if not events_path.is_file():
            continue
        found.append((trades_path.stat().st_mtime, trades_path.parent))
    found.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    out: list[Path] = []
    for _, p in found:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max_sessions:
            break
    return out


def build_price_timeline_from_events_csv(
    events_csv: Path,
    symbols: set[str],
) -> dict[str, list[tuple[float, float]]]:
    tl: dict[str, list[tuple[float, float]]] = defaultdict(list)
    if not events_csv.is_file():
        return tl
    with events_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = str(row.get("symbol") or "").strip()
            if sym not in symbols:
                continue
            et = str(row.get("event_type") or "")
            if et not in ("candidate", "accepted"):
                continue
            px = as_float(row.get("current_price"))
            if not px or px <= 0:
                continue
            ts_raw = str(row.get("event_time") or row.get("entry_time") or "")
            ts = parse_ts(ts_raw)
            if ts > 0:
                tl[sym].append((ts, float(px)))
    for sym in tl:
        tl[sym].sort(key=lambda x: x[0])
    return tl


def price_at_horizon(
    timeline: Sequence[tuple[float, float]],
    *,
    base_ts: float,
    entry_price: float,
    horizon_sec: float,
    session_end_ts: float,
) -> Optional[float]:
    target = base_ts + horizon_sec
    if target > session_end_ts:
        target = session_end_ts
    chosen: Optional[float] = None
    for ts, px in timeline:
        if ts >= target:
            chosen = px
            break
    if chosen is None and timeline:
        for ts, px in reversed(timeline):
            if ts >= base_ts:
                chosen = px
                break
    return pnl_pct(entry_price, chosen) if chosen is not None else None


def best_pnl_in_window(
    timeline: Sequence[tuple[float, float]],
    *,
    base_ts: float,
    entry_price: float,
    window_sec: float,
    session_end_ts: float,
) -> Optional[float]:
    end = min(base_ts + window_sec, session_end_ts)
    best: Optional[float] = None
    for ts, px in timeline:
        if ts < base_ts:
            continue
        if ts > end:
            break
        p = pnl_pct(entry_price, px)
        best = p if best is None else max(best, p)
    return best


def session_end_ts_from_trades(trades: Sequence[Mapping[str, Any]]) -> float:
    mx = 0.0
    for t in trades:
        mx = max(mx, parse_ts(str(t.get("close_time") or "")))
    return mx


def enrich_trade(
    trade: Mapping[str, Any],
    timeline: Sequence[tuple[float, float]],
    *,
    session_end_ts: float,
    session_id: str,
) -> dict[str, Any]:
    entry_px = as_float(trade.get("entry_price")) or 0.0
    exit_px = as_float(trade.get("close_price")) or 0.0
    entry_pnl = 0.0
    exit_pnl = as_float(trade.get("realized_pnl_pct"))
    if exit_pnl is None:
        exit_pnl = pnl_pct(entry_px, exit_px)
    mfe = as_float(trade.get("mfe_pct")) or 0.0
    mae = as_float(trade.get("mae_pct")) or 0.0
    hold = as_float(trade.get("hold_duration_sec")) or 0.0
    reason = str(trade.get("close_reason") or "")
    had_take = str(trade.get("had_take_before_exit") or "").lower() in ("true", "1", "yes")
    take_pnl = as_float(trade.get("take_pnl_pct"))
    close_ts = parse_ts(str(trade.get("close_time") or ""))

    capture: Optional[float] = None
    if mfe > 0.01:
        capture = round(exit_pnl / mfe, 4)
    elif mfe <= 0 and exit_pnl <= 0:
        capture = 1.0

    post: dict[str, Optional[float]] = {}
    post_best: dict[str, Optional[float]] = {}
    for h in POST_EXIT_HORIZONS_SEC:
        post[f"post_exit_pnl_{h}s"] = price_at_horizon(
            timeline,
            base_ts=close_ts,
            entry_price=entry_px,
            horizon_sec=float(h),
            session_end_ts=session_end_ts,
        )
        post_best[f"post_exit_best_pnl_{h}s"] = best_pnl_in_window(
            timeline,
            base_ts=close_ts,
            entry_price=entry_px,
            window_sec=float(h),
            session_end_ts=session_end_ts,
        )

    left_on_table = round(max(0.0, mfe - exit_pnl), 4) if mfe > 0 else None
    post_follow = None
    pb60 = post_best.get("post_exit_best_pnl_60s")
    if pb60 is not None:
        post_follow = round(pb60 - exit_pnl, 4)

    missed_large_mfe = bool(mfe >= MFE_LARGE_PCT and (capture is None or capture < MFE_CAPTURE_LOW))
    continued_after_exit = bool(
        pb60 is not None and pb60 >= exit_pnl + POST_EXIT_FOLLOWTHROUGH_PCT
    )

    return {
        "session_id": session_id,
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "close_time": trade.get("close_time"),
        "entry_price": entry_px,
        "close_price": exit_px,
        "entry_pnl": entry_pnl,
        "exit_pnl": exit_pnl,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "exit_reason": reason,
        "hold_sec": hold,
        "had_take_before_exit": had_take,
        "take_pnl_pct": take_pnl,
        "take_before_exit": had_take,
        "mfe_capture_rate": capture,
        "left_on_table_pct": left_on_table,
        "missed_large_mfe": missed_large_mfe,
        "continued_after_exit_60s": continued_after_exit,
        **post,
        **post_best,
        "post_exit_followthrough_delta_60s": post_follow,
    }


def summarize_by_exit_reason(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_reason[str(r.get("exit_reason") or "unknown")].append(dict(r))

    out: list[dict[str, Any]] = []
    for reason, grp in sorted(by_reason.items()):
        pnls = [float(r["exit_pnl"]) for r in grp if r.get("exit_pnl") is not None]
        mfes = [float(r["mfe_pct"]) for r in grp if as_float(r.get("mfe_pct")) is not None]
        caps = [float(r["mfe_capture_rate"]) for r in grp if as_float(r.get("mfe_capture_rate")) is not None]
        pb60 = [float(r["post_exit_best_pnl_60s"]) for r in grp if as_float(r.get("post_exit_best_pnl_60s")) is not None]
        pb180 = [float(r["post_exit_best_pnl_180s"]) for r in grp if as_float(r.get("post_exit_best_pnl_180s")) is not None]
        out.append(
            {
                "exit_reason": reason,
                "trade_count": len(grp),
                "avg_exit_pnl": round(statistics.mean(pnls), 4) if pnls else None,
                "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else None,
                "avg_mfe_capture_rate": round(statistics.mean(caps), 4) if caps else None,
                "median_mfe_capture_rate": round(statistics.median(caps), 4) if caps else None,
                "avg_post_exit_best_pnl_60s": round(statistics.mean(pb60), 4) if pb60 else None,
                "avg_post_exit_best_pnl_180s": round(statistics.mean(pb180), 4) if pb180 else None,
                "missed_large_mfe_count": sum(1 for r in grp if r.get("missed_large_mfe")),
                "continued_after_exit_60s_count": sum(1 for r in grp if r.get("continued_after_exit_60s")),
            }
        )
    return out


def overlap_replace_analysis(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    overlap = [r for r in rows if str(r.get("exit_reason") or "") == "overlap_replaced_review"]
    out: list[dict[str, Any]] = []
    for r in overlap:
        pb60 = as_float(r.get("post_exit_best_pnl_60s"))
        exit_pnl = float(r.get("exit_pnl") or 0)
        cf = round((pb60 - exit_pnl), 4) if pb60 is not None else None
        out.append(
            {
                **{k: r[k] for k in ("session_id", "symbol", "entry_time", "close_time", "exit_pnl", "mfe_pct")},
                "post_exit_best_pnl_60s": pb60,
                "counterfactual_vs_exit_60s": cf,
                "overlap_left_on_table": bool(cf is not None and cf >= OVERLAP_COUNTERFACTUAL_PCT),
            }
        )
    return out


def momentum_fade_followthrough(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    mom = [r for r in rows if "momentum_fade" in str(r.get("exit_reason") or "")]
    out: list[dict[str, Any]] = []
    for r in mom:
        pb60 = as_float(r.get("post_exit_best_pnl_60s"))
        pb180 = as_float(r.get("post_exit_best_pnl_180s"))
        exit_pnl = float(r.get("exit_pnl") or 0)
        rebound = bool(pb60 is not None and pb60 >= exit_pnl + POST_EXIT_FOLLOWTHROUGH_PCT)
        out.append(
            {
                **{k: r[k] for k in ("session_id", "symbol", "entry_time", "close_time", "exit_reason", "exit_pnl", "mfe_pct", "mfe_capture_rate", "had_take_before_exit")},
                "post_exit_best_pnl_60s": pb60,
                "post_exit_best_pnl_180s": pb180,
                "rebound_after_fade_60s": rebound,
            }
        )
    return out


def build_whatif_hypotheses(
    trade_rows: Sequence[Mapping[str, Any]],
    reason_rows: Sequence[Mapping[str, Any]],
    overlap_rows: Sequence[Mapping[str, Any]],
    mom_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    hyps: list[dict[str, Any]] = []
    caps = [float(r["mfe_capture_rate"]) for r in trade_rows if as_float(r.get("mfe_capture_rate")) is not None]
    avg_cap = statistics.mean(caps) if caps else None

    mom_rebound = sum(1 for r in mom_rows if r.get("rebound_after_fade_60s"))
    mom_n = len(mom_rows)
    overlap_miss = sum(1 for r in overlap_rows if r.get("overlap_left_on_table"))
    overlap_n = len(overlap_rows)

    if avg_cap is not None and avg_cap < MFE_CAPTURE_LOW:
        hyps.append(
            {
                "hypothesis_id": "widen_mfe_giveback",
                "category": "exit_timing",
                "what_if": "When MFE >= 0.25%, allow wider giveback before structural fade exits",
                "evidence": f"avg_mfe_capture_rate={avg_cap:.2%}",
                "implementation": "review_only_not_in_yaml",
            }
        )

    if mom_n >= 5 and mom_rebound / mom_n >= MOMENTUM_FADE_REBOUND_RATE:
        hyps.append(
            {
                "hypothesis_id": "delay_momentum_fade",
                "category": "momentum_fade_exit",
                "what_if": "Delay momentum_fade_exit confirmation by ~1-2 ticks or relax fade ratio slightly",
                "evidence": f"rebound_after_fade_60s={mom_rebound}/{mom_n}",
                "implementation": "review_only_not_in_yaml",
            }
        )

    if overlap_n >= 5 and overlap_miss / overlap_n >= 0.35:
        hyps.append(
            {
                "hypothesis_id": "overlap_compare_hold",
                "category": "overlap_replace",
                "what_if": "On overlap candidate, compare hold vs replace instead of immediate overlap_replaced_review close",
                "evidence": f"overlap_left_on_table_60s={overlap_miss}/{overlap_n}",
                "implementation": "review_only_not_in_yaml",
            }
        )

    take_rows = [r for r in trade_rows if r.get("had_take_before_exit")]
    if take_rows:
        take_cap = [float(r["mfe_capture_rate"]) for r in take_rows if as_float(r.get("mfe_capture_rate"))]
        if take_cap and statistics.mean(take_cap) < MFE_CAPTURE_LOW:
            hyps.append(
                {
                    "hypothesis_id": "take_to_trail",
                    "category": "take_path",
                    "what_if": "After take signal, switch to trailing stop from MFE peak instead of immediate fade exit",
                    "evidence": f"take_trades_avg_capture={statistics.mean(take_cap):.2%}",
                    "implementation": "review_only_not_in_yaml",
                }
            )

    fade_reason = next((r for r in reason_rows if r.get("exit_reason") == "momentum_fade_exit"), None)
    if fade_reason and as_float(fade_reason.get("avg_mfe_capture_rate")) is not None:
        if float(fade_reason["avg_mfe_capture_rate"]) < MFE_CAPTURE_LOW:
            hyps.append(
                {
                    "hypothesis_id": "momentum_fade_capture_low",
                    "category": "momentum_fade_exit",
                    "what_if": "momentum_fade_exit exits before capturing available MFE on favorable trades",
                    "evidence": f"momentum_fade_avg_capture={fade_reason['avg_mfe_capture_rate']}",
                    "implementation": "review_only_not_in_yaml",
                }
            )

    if not hyps:
        hyps.append(
            {
                "hypothesis_id": "no_strong_exit_hypothesis",
                "category": "none",
                "what_if": "No dominant exit pathology; prioritize Entry/Universe review",
                "evidence": "thresholds_not_met",
                "implementation": "review_only",
            }
        )
    return hyps


def determine_verdict(
    trade_rows: Sequence[Mapping[str, Any]],
    reason_rows: Sequence[Mapping[str, Any]],
    overlap_rows: Sequence[Mapping[str, Any]],
    mom_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not trade_rows:
        return "exit_logic_reasonable", ["no structural trades in selected sessions"]

    caps = [float(r["mfe_capture_rate"]) for r in trade_rows if as_float(r.get("mfe_capture_rate")) is not None]
    avg_cap = statistics.mean(caps) if caps else None
    missed = sum(1 for r in trade_rows if r.get("missed_large_mfe"))
    notes.append(f"trades={len(trade_rows)} avg_mfe_capture={avg_cap:.2%}" if avg_cap else f"trades={len(trade_rows)}")

    overlap_miss = sum(1 for r in overlap_rows if r.get("overlap_left_on_table"))
    overlap_n = len(overlap_rows)
    mom_rebound = sum(1 for r in mom_rows if r.get("rebound_after_fade_60s"))
    mom_n = len(mom_rows)

    overlap_bad = overlap_n >= 5 and overlap_miss / overlap_n >= 0.35
    mom_fast = mom_n >= 5 and mom_rebound / mom_n >= MOMENTUM_FADE_REBOUND_RATE
    exit_bad = avg_cap is not None and avg_cap < MFE_CAPTURE_LOW and missed >= max(3, len(trade_rows) * 0.15)

    if overlap_bad and (exit_bad or mom_fast):
        notes.append(f"overlap_miss={overlap_miss}/{overlap_n} mom_rebound={mom_rebound}/{mom_n}")
        return "overlap_replace_needs_revision", notes
    if mom_fast and not exit_bad:
        notes.append(f"mom_rebound={mom_rebound}/{mom_n}")
        return "momentum_fade_too_fast", notes
    if overlap_bad:
        notes.append(f"overlap_miss={overlap_miss}/{overlap_n}")
        return "overlap_replace_needs_revision", notes
    if exit_bad:
        notes.append(f"missed_large_mfe={missed}")
        return "exit_logic_needs_revision", notes
    if avg_cap is not None and avg_cap >= 0.45:
        return "exit_logic_reasonable", notes + [f"avg_capture={avg_cap:.2%}"]
    return "exit_logic_reasonable", notes + ["mixed signals; Entry/Universe may dominate"]


def analyze_sessions(session_dirs: Sequence[Path]) -> dict[str, Any]:
    all_trades: list[dict[str, Any]] = []
    session_meta: list[dict[str, Any]] = []

    for sdir in session_dirs:
        trades_path = sdir / "structural_trades.csv"
        trades_raw = load_structural_trades(trades_path)
        if not trades_raw:
            continue
        session_id = str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        symbols = {str(t.get("symbol") or "") for t in trades_raw}
        events_csv = sdir / "small_paper_events.csv"
        tl_map = build_price_timeline_from_events_csv(events_csv, symbols)
        end_ts = session_end_ts_from_trades(trades_raw)

        for t in trades_raw:
            sym = str(t.get("symbol") or "")
            enriched = enrich_trade(
                t,
                tl_map.get(sym, []),
                session_end_ts=end_ts,
                session_id=session_id,
            )
            all_trades.append(enriched)

        session_meta.append(
            {
                "session_id": session_id,
                "session_dir": str(sdir),
                "trade_count": len(trades_raw),
                "exit_reason_counts": dict(Counter(str(t.get("close_reason") or "") for t in trades_raw)),
            }
        )

    reason_rows = summarize_by_exit_reason(all_trades)
    overlap_rows = overlap_replace_analysis(all_trades)
    mom_rows = momentum_fade_followthrough(all_trades)
    hypotheses = build_whatif_hypotheses(all_trades, reason_rows, overlap_rows, mom_rows)
    verdict, verdict_notes = determine_verdict(all_trades, reason_rows, overlap_rows, mom_rows)

    post_exit_rows = [
        {
            "session_id": r.get("session_id"),
            "symbol": r.get("symbol"),
            "exit_reason": r.get("exit_reason"),
            "exit_pnl": r.get("exit_pnl"),
            "post_exit_pnl_30s": r.get("post_exit_pnl_30s"),
            "post_exit_pnl_60s": r.get("post_exit_pnl_60s"),
            "post_exit_pnl_180s": r.get("post_exit_pnl_180s"),
            "post_exit_best_pnl_60s": r.get("post_exit_best_pnl_60s"),
            "post_exit_best_pnl_180s": r.get("post_exit_best_pnl_180s"),
            "continued_after_exit_60s": r.get("continued_after_exit_60s"),
        }
        for r in all_trades
    ]

    return {
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "sessions": session_meta,
        "trade_rows": all_trades,
        "exit_reason_capture": reason_rows,
        "overlap_replace_followup": overlap_rows,
        "momentum_fade_followup": mom_rows,
        "post_exit_followthrough": post_exit_rows,
        "whatif_hypotheses": hypotheses,
        "aggregate": {
            "trade_count": len(all_trades),
            "avg_exit_pnl": round(
                statistics.mean(float(r["exit_pnl"]) for r in all_trades), 4
            )
            if all_trades
            else None,
            "avg_mfe_pct": round(
                statistics.mean(float(r["mfe_pct"]) for r in all_trades if as_float(r.get("mfe_pct")) is not None),
                4,
            )
            if all_trades
            else None,
            "avg_mfe_capture_rate": round(
                statistics.mean(
                    float(r["mfe_capture_rate"])
                    for r in all_trades
                    if as_float(r.get("mfe_capture_rate")) is not None
                ),
                4,
            )
            if all_trades
            else None,
            "missed_large_mfe_count": sum(1 for r in all_trades if r.get("missed_large_mfe")),
            "continued_after_exit_60s_count": sum(1 for r in all_trades if r.get("continued_after_exit_60s")),
        },
    }
