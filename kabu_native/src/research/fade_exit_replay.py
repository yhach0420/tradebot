"""
Phase 121: What-if replay for momentum_fade / price_momentum_fade exits.
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from research.mfe_mae_exit_review import (
    as_float,
    build_price_timeline_from_events_csv,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    price_at_horizon,
    session_end_ts_from_trades,
)

FADE_EXIT_REASONS = frozenset({"momentum_fade_exit", "price_momentum_fade_exit"})

SCENARIOS: tuple[tuple[str, str], ...] = (
    ("A_current", "current_exit"),
    ("B_hold_30s", "hold_plus_30s"),
    ("C_hold_60s", "hold_plus_60s"),
    ("D_hold_120s", "hold_plus_120s"),
    ("E_giveback_25", "mfe_peak_giveback_25pct"),
    ("F_giveback_40", "mfe_peak_giveback_40pct"),
)

HOLD_DELAYS = {"B_hold_30s": 30.0, "C_hold_60s": 60.0, "D_hold_120s": 120.0}
GIVEBACK = {"E_giveback_25": 0.25, "F_giveback_40": 0.40}


def is_fade_trade(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("close_reason") or "") in FADE_EXIT_REASONS


def slice_timeline(
    timeline: Sequence[tuple[float, float]],
    *,
    start_ts: float,
    end_ts: float,
) -> list[tuple[float, float]]:
    return [(ts, px) for ts, px in timeline if start_ts <= ts <= end_ts]


def simulate_giveback_exit(
    timeline: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_price: float,
    giveback_frac: float,
    session_end_ts: float,
) -> tuple[float, float, float, float]:
    """Walk from entry; exit when pnl <= peak * (1 - giveback_frac)."""
    peak = 0.0
    trough = 0.0
    exit_pnl = 0.0
    exit_ts = entry_ts
    triggered = False

    for ts, px in timeline:
        if ts < entry_ts:
            continue
        if ts > session_end_ts:
            break
        p = pnl_pct(entry_price, px)
        peak = max(peak, p)
        trough = min(trough, p)
        if peak > 0.01 and p <= peak * (1.0 - giveback_frac):
            exit_pnl = p
            exit_ts = ts
            triggered = True
            break

    if not triggered:
        last_px: Optional[float] = None
        last_ts = entry_ts
        for ts, px in timeline:
            if entry_ts <= ts <= session_end_ts:
                last_px = px
                last_ts = ts
                p = pnl_pct(entry_price, px)
                peak = max(peak, p)
                trough = min(trough, p)
        if last_px is not None:
            exit_pnl = pnl_pct(entry_price, last_px)
            exit_ts = last_ts

    return exit_pnl, exit_ts, peak, trough


def replay_trade_scenarios(
    trade: Mapping[str, Any],
    timeline: Sequence[tuple[float, float]],
    *,
    session_end_ts: float,
    session_id: str,
) -> dict[str, Any]:
    entry_px = as_float(trade.get("entry_price")) or 0.0
    entry_ts = parse_ts(str(trade.get("entry_time") or ""))
    close_ts = parse_ts(str(trade.get("close_time") or ""))
    baseline_pnl = as_float(trade.get("realized_pnl_pct")) or 0.0
    baseline_hold = as_float(trade.get("hold_duration_sec")) or max(0.0, close_ts - entry_ts)
    mfe = as_float(trade.get("mfe_pct")) or 0.0
    mae = as_float(trade.get("mae_pct")) or 0.0
    reason = str(trade.get("close_reason") or "")
    had_take = str(trade.get("had_take_before_exit") or "").lower() in ("true", "1", "yes")

    row: dict[str, Any] = {
        "session_id": session_id,
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "close_time": trade.get("close_time"),
        "exit_reason": reason,
        "entry_price": entry_px,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "had_take_before_exit": had_take,
        "baseline_hold_sec": baseline_hold,
    }

    scenario_pnls: dict[str, float] = {}

    for sid, _ in SCENARIOS:
        if sid == "A_current":
            pnl = baseline_pnl
            hold = baseline_hold
            dd = mae
        elif sid in HOLD_DELAYS:
            delay = HOLD_DELAYS[sid]
            sim = price_at_horizon(
                timeline,
                base_ts=close_ts,
                entry_price=entry_px,
                horizon_sec=delay,
                session_end_ts=session_end_ts,
            )
            pnl = sim if sim is not None else baseline_pnl
            hold = baseline_hold + delay
            path = slice_timeline(timeline, start_ts=entry_ts, end_ts=close_ts + delay)
            dd = min((pnl_pct(entry_px, px) for _, px in path), default=mae)
        elif sid in GIVEBACK:
            gb = GIVEBACK[sid]
            pnl, ex_ts, peak, trough = simulate_giveback_exit(
                timeline,
                entry_ts=entry_ts,
                entry_price=entry_px,
                giveback_frac=gb,
                session_end_ts=session_end_ts,
            )
            hold = max(0.0, ex_ts - entry_ts)
            dd = trough
        else:
            pnl = baseline_pnl
            hold = baseline_hold
            dd = mae

        cap = round(pnl / mfe, 4) if mfe > 0.01 else None
        scenario_pnls[sid] = pnl
        row[f"{sid}_pnl"] = pnl
        row[f"{sid}_hold_sec"] = round(hold, 1)
        row[f"{sid}_capture_rate"] = cap
        row[f"{sid}_max_drawdown_proxy"] = round(dd, 4)

    for sid, _ in SCENARIOS:
        if sid == "A_current":
            continue
        pnl = scenario_pnls[sid]
        row[f"{sid}_delta_vs_A"] = round(pnl - baseline_pnl, 4)
        row[f"{sid}_worsened"] = pnl < baseline_pnl
        row[f"{sid}_loss_expanded"] = pnl < baseline_pnl and pnl < 0

    return row


def summarize_scenario(rows: Sequence[Mapping[str, Any]], scenario_id: str) -> dict[str, Any]:
    pnls = [float(r[f"{scenario_id}_pnl"]) for r in rows if r.get(f"{scenario_id}_pnl") is not None]
    holds = [float(r[f"{scenario_id}_hold_sec"]) for r in rows if r.get(f"{scenario_id}_hold_sec") is not None]
    caps = [
        float(r[f"{scenario_id}_capture_rate"])
        for r in rows
        if as_float(r.get(f"{scenario_id}_capture_rate")) is not None
    ]
    dds = [float(r[f"{scenario_id}_max_drawdown_proxy"]) for r in rows if r.get(f"{scenario_id}_max_drawdown_proxy") is not None]
    worsened = sum(1 for r in rows if r.get(f"{scenario_id}_worsened"))
    loss_exp = sum(1 for r in rows if r.get(f"{scenario_id}_loss_expanded"))
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "scenario_id": scenario_id,
        "scenario_label": dict(SCENARIOS).get(scenario_id, scenario_id),
        "trade_count": n,
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "total_pnl": round(sum(pnls), 4) if pnls else None,
        "win_rate": round(wins / n, 4) if n else None,
        "avg_hold_sec": round(statistics.mean(holds), 1) if holds else None,
        "avg_max_drawdown_proxy": round(statistics.mean(dds), 4) if dds else None,
        "avg_capture_rate": round(statistics.mean(caps), 4) if caps else None,
        "worsened_vs_A_count": worsened,
        "worsened_vs_A_rate": round(worsened / n, 4) if n else None,
        "loss_expanded_count": loss_exp,
        "loss_expanded_rate": round(loss_exp / n, 4) if n else None,
        "avg_delta_vs_A": (
            0.0
            if scenario_id == "A_current"
            else round(
                statistics.mean(float(r[f"{scenario_id}_delta_vs_A"]) for r in rows), 4
            )
            if rows
            else None
        ),
    }


def determine_verdict(
    summaries: Sequence[Mapping[str, Any]],
    detail_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_id = {s["scenario_id"]: s for s in summaries}
    base = by_id.get("A_current") or {}
    base_total = float(base.get("total_pnl") or 0)
    base_avg = float(base.get("avg_pnl") or 0)
    n = int(base.get("trade_count") or 0)

    if n == 0:
        return "current_fade_exit_best", ["no fade trades"]

    best_id = max(
        (s["scenario_id"] for s in summaries),
        key=lambda sid: float(by_id[sid].get("total_pnl") or -1e9),
    )
    best = by_id[best_id]
    best_total = float(best.get("total_pnl") or 0)
    improvement = best_total - base_total
    notes.append(
        f"fade_trades={n} A_total={base_total:.4f} best={best_id} total={best_total:.4f} delta={improvement:.4f}"
    )

    hold_ids = ("B_hold_30s", "C_hold_60s", "D_hold_120s")
    hold_better = any(
        float(by_id[sid].get("total_pnl") or 0) > base_total + 0.5 for sid in hold_ids if sid in by_id
    )
    giveback_ids = ("E_giveback_25", "F_giveback_40")
    gb_better = any(
        float(by_id[sid].get("total_pnl") or 0) > base_total + 0.5 for sid in giveback_ids if sid in by_id
    )

    take_rows = [r for r in detail_rows if r.get("had_take_before_exit")]
    take_gb_better = False
    if take_rows and gb_better:
        for gid in giveback_ids:
            take_delta = statistics.mean(float(r.get(f"{gid}_delta_vs_A") or 0) for r in take_rows)
            if take_delta > 0.02:
                take_gb_better = True
                notes.append(f"take_trades_{gid}_avg_delta={take_delta:.4f}")

    if best_id == "A_current" or improvement < 0.3:
        if not hold_better and not gb_better:
            return "current_fade_exit_best", notes
        if hold_better and not gb_better:
            return "hold_longer_not_helpful", notes + ["hold delays help total but giveback does not dominate"]

    if hold_better and not gb_better:
        best_hold = max(hold_ids, key=lambda sid: float(by_id[sid].get("total_pnl") or -1e9))
        wr = float(by_id[best_hold].get("worsened_vs_A_rate") or 0)
        if wr > 0.55:
            return "hold_longer_not_helpful", notes + [f"{best_hold} worsened_rate={wr:.1%}"]
        return "fade_exit_needs_revision", notes + [f"hold_longer_best={best_hold}"]

    if take_gb_better and gb_better:
        return "trail_after_take_promising", notes + ["giveback scenarios help especially with take_before_exit"]

    if gb_better:
        best_gb = max(giveback_ids, key=lambda sid: float(by_id[sid].get("total_pnl") or -1e9))
        wr = float(by_id[best_gb].get("worsened_vs_A_rate") or 0)
        if wr > 0.5:
            return "fade_exit_needs_revision", notes + [f"{best_gb} higher total but worsened={wr:.1%}"]
        return "fade_exit_needs_revision", notes + [f"giveback_best={best_gb}"]

    if hold_better:
        return "fade_exit_needs_revision", notes

    return "current_fade_exit_best", notes


def analyze_fade_replay(session_dirs: Sequence[Any]) -> dict[str, Any]:
    from pathlib import Path

    detail_rows: list[dict[str, Any]] = []

    for sdir in session_dirs:
        sdir = Path(sdir)
        trades_path = sdir / "structural_trades.csv"
        trades_raw = load_structural_trades(trades_path)
        fade_trades = [t for t in trades_raw if is_fade_trade(t)]
        if not fade_trades:
            continue
        session_id = str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        symbols = {str(t.get("symbol") or "") for t in fade_trades}
        tl_map = build_price_timeline_from_events_csv(sdir / "small_paper_events.csv", symbols)
        end_ts = session_end_ts_from_trades(trades_raw)

        for t in fade_trades:
            sym = str(t.get("symbol") or "")
            detail_rows.append(
                replay_trade_scenarios(
                    t,
                    tl_map.get(sym, []),
                    session_end_ts=end_ts,
                    session_id=session_id,
                )
            )

    summaries = [summarize_scenario(detail_rows, sid) for sid, _ in SCENARIOS]
    verdict, notes = determine_verdict(summaries, detail_rows)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "scenario_summaries": summaries,
        "trade_details": detail_rows,
        "fade_trade_count": len(detail_rows),
        "by_exit_reason": {
            reason: sum(1 for r in detail_rows if r.get("exit_reason") == reason)
            for reason in sorted(FADE_EXIT_REASONS)
        },
    }
