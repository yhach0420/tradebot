"""Strict EXIT baseline parity: actual runtime trade ↔ X0 replay (position_id first)."""
from __future__ import annotations

import statistics
from typing import Any, Optional, Sequence

from research.pbv2_zero_base_revalidation.util import pnl_5bps, yen100
from research.price_flow_exit.constants import (
    HARD_STOP_PCT,
    NP_CURRENT_PNL_MAX,
    NP_REQUIRED_MFE_PCT,
    NP_START_SEC,
)
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.path_mfe import ExitResult, PathBar, _ret, _session_close_time, bars_after_entry
from research.price_flow_exit_integrity.actuals import ActualTrade
from research.price_flow_exit_integrity.constants import PATH_MAX_SEC, X0_REASONS
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier


def reason_family(reason: str) -> str:
    r = (reason or "").lower()
    if "stop" in r:
        return "stop"
    if "trail" in r:
        return "trail"
    if "no_progress" in r:
        return "no_progress"
    if "session" in r or "morning" in r or "afternoon" in r:
        return "session"
    return "other"


def simulate_x0_runtime_proxy(
    entry: FixedEntry,
    path: Sequence[PathBar],
    *,
    activate: Optional[float] = None,
    giveback: Optional[float] = None,
) -> ExitResult:
    """X0 logic with CurrentPrice (runtime observer path), optional actual trail params.

    Does not modify research Bid-based X0; used only for baseline parity matching.
    """
    if not path:
        return ExitResult(
            entry.entry_time, entry.entry_price, "PATH_EMPTY", 0.0, 0.0, 0.0, False, ["PATH_EMPTY"], False, True
        )
    if activate is None or giveback is None:
        a, g, _ = trailing_params_for_board_tier(entry.entry_imbalance_percentile)
        activate = a if activate is None else activate
        giveback = g if giveback is None else giveback
    stop_px = entry.entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    trail_on = False
    mfe = 0.0
    close_at = _session_close_time(entry.entry_time)
    for b in path:
        px = float(b.px)
        hold = (b.t - entry.entry_time).total_seconds()
        pnl = _ret(entry.entry_price, px)
        mfe = max(mfe, pnl)
        peak_pnl = max(peak_pnl, pnl)
        if px <= stop_px or pnl <= -HARD_STOP_PCT:
            return ExitResult(
                b.t, px, "stop_hit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["stop_hit"], False, False
            )
        if hold >= NP_START_SEC and mfe < NP_REQUIRED_MFE_PCT and pnl < NP_CURRENT_PNL_MAX and not trail_on:
            return ExitResult(
                b.t,
                px,
                "no_progress_exit",
                yen100(entry.entry_price, px),
                pnl_5bps(entry.entry_price, px),
                hold,
                False,
                ["no_progress_exit"],
                False,
                False,
            )
        if peak_pnl >= float(activate):
            trail_on = True
            if pnl <= peak_pnl * float(giveback):
                return ExitResult(
                    b.t,
                    px,
                    "trailing_mfe_exit",
                    yen100(entry.entry_price, px),
                    pnl_5bps(entry.entry_price, px),
                    hold,
                    True,
                    ["trailing_mfe_exit"],
                    False,
                    False,
                )
        if close_at and b.t >= close_at:
            reason = "morning_session_close" if entry.entry_time.hour < 12 else "afternoon_session_close"
            return ExitResult(
                b.t, px, reason, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [reason], False, False
            )
    b = path[-1]
    px = float(b.px)
    hold = (b.t - entry.entry_time).total_seconds()
    return ExitResult(
        b.t, px, "path_end", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["path_end"], False, True
    )


def _pctiles(xs: Sequence[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "median": None, "p25": None, "p75": None, "mean": None}
    s = sorted(xs)
    n = len(s)

    def q(p: float) -> float:
        i = min(n - 1, max(0, int(round((n - 1) * p))))
        return round(s[i], 4)

    return {
        "n": n,
        "median": round(statistics.median(s), 4),
        "p25": q(0.25),
        "p75": q(0.75),
        "mean": round(sum(s) / n, 4),
    }


def run_baseline_parity(
    actuals: Sequence[ActualTrade],
    push_by_day: dict[str, dict],
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    unmatched_actual: list[dict[str, Any]] = []
    bars_cache: dict[tuple[str, str], list] = {}

    for a in actuals:
        ticks = (push_by_day.get(a.day) or {}).get(a.symbol) or []
        if not ticks:
            unmatched_actual.append(
                {
                    "position_id": a.position_id,
                    "day": a.day,
                    "symbol": a.symbol,
                    "reason": "NO_PUSH",
                    "exit_reason": a.exit_reason,
                }
            )
            continue
        key = (a.day, a.symbol)
        if key not in bars_cache:
            bars_cache[key] = aggregate_to_seconds(ticks)
        path = bars_after_entry(bars_cache[key], a.entry_time, max_sec=PATH_MAX_SEC)
        if not path:
            unmatched_actual.append(
                {
                    "position_id": a.position_id,
                    "day": a.day,
                    "symbol": a.symbol,
                    "reason": "EMPTY_PATH",
                    "exit_reason": a.exit_reason,
                }
            )
            continue
        entry = FixedEntry(
            day=a.day,
            symbol=a.symbol,
            entry_time=a.entry_time,
            entry_price=a.entry_price,
            entry_method="PBv2",
            cohort="BASELINE",
            pbv2=True,
            entry_imbalance_percentile=a.entry_imbalance_percentile,
            setup_id=a.position_id,
            accept=True,
            actual_exit_reason=a.exit_reason,
            actual_exit_price=a.exit_price,
            actual_pnl_5bps=None,
        )
        x0 = simulate_x0_runtime_proxy(
            entry,
            path,
            activate=a.trail_activate_pct,
            giveback=a.trail_giveback_frac,
        )
        dt = None
        if a.exit_time is not None:
            dt = (x0.exit_time - a.exit_time).total_seconds()
        dpx = None
        if a.exit_price is not None:
            dpx = x0.exit_price - a.exit_price
        dpnl = None
        if a.pnl_yen_100 is not None:
            dpnl = x0.pnl_raw - a.pnl_yen_100
        exact = a.exit_reason == x0.exit_reason
        fam_ok = reason_family(a.exit_reason) == reason_family(x0.exit_reason)
        matches.append(
            {
                "position_id": a.position_id,
                "day": a.day,
                "symbol": a.symbol,
                "entry_time": a.entry_time.isoformat(),
                "entry_price": a.entry_price,
                "actual_exit_time": a.exit_time.isoformat() if a.exit_time else None,
                "replay_exit_time": x0.exit_time.isoformat(),
                "actual_exit_price": a.exit_price,
                "replay_exit_price": x0.exit_price,
                "actual_exit_reason": a.exit_reason,
                "replay_exit_reason": x0.exit_reason,
                "actual_reason_family": reason_family(a.exit_reason),
                "replay_reason_family": reason_family(x0.exit_reason),
                "exact_reason_match": exact,
                "family_reason_match": fam_ok,
                "exit_time_diff_sec": dt,
                "exit_price_diff": dpx,
                "pnl_diff_yen100": dpnl,
                "actual_pnl_yen_100": a.pnl_yen_100,
                "replay_pnl_yen_100": x0.pnl_raw,
                "actual_hold_sec": a.hold_sec,
                "replay_hold_sec": x0.hold_sec,
                "x0_vocab": a.exit_reason in X0_REASONS,
                "match_key": "position_id",
            }
        )

    # Gate on all matched; also report utiliable (X0 vocab) subset
    n_actual = len(actuals)
    n_match = len(matches)
    util = [m for m in matches if m["x0_vocab"]]
    gate_pool = util if len(util) >= 100 or (n_actual > 0 and len(util) / n_actual >= 0.9) else matches

    exact_rate = sum(1 for m in gate_pool if m["exact_reason_match"]) / max(1, len(gate_pool))
    fam_rate = sum(1 for m in gate_pool if m["family_reason_match"]) / max(1, len(gate_pool))
    dt_abs = [abs(float(m["exit_time_diff_sec"])) for m in gate_pool if m["exit_time_diff_sec"] is not None]
    pnl_abs = [abs(float(m["pnl_diff_yen100"])) for m in gate_pool if m["pnl_diff_yen100"] is not None]
    ap = sum(float(m["actual_pnl_yen_100"] or 0) for m in gate_pool)
    rp = sum(float(m["replay_pnl_yen_100"] or 0) for m in gate_pool)
    total_pnl_pct = abs(rp - ap) / abs(ap) if abs(ap) > 1e-9 else None

    cover_ok = n_match >= 100 or (n_actual > 0 and n_match / n_actual >= 0.90)
    dt_med = statistics.median(dt_abs) if dt_abs else None
    pnl_med = statistics.median(pnl_abs) if pnl_abs else None
    gates = {
        "coverage": cover_ok,
        "family_ge_90": fam_rate >= 0.90,
        "exact_ge_80": exact_rate >= 0.80,
        "exit_time_med_le_5": dt_med is not None and dt_med <= 5.0,
        "pnl_med_le_100": pnl_med is not None and pnl_med <= 100.0,
        "total_pnl_pct_le_5": total_pnl_pct is not None and total_pnl_pct <= 0.05,
    }
    gate_ok = all(gates.values())
    return {
        "n_actual": n_actual,
        "n_matched": n_match,
        "n_unmatched_actual": len(unmatched_actual),
        "n_unmatched_replay": 0,
        "n_gate_pool": len(gate_pool),
        "n_x0_vocab": len(util),
        "coverage_rate": round(n_match / max(1, n_actual), 4),
        "exact_reason_match_rate": round(exact_rate, 4),
        "family_reason_match_rate": round(fam_rate, 4),
        "exit_time_diff_sec": _pctiles(dt_abs),
        "exit_price_diff": _pctiles([float(m["exit_price_diff"]) for m in gate_pool if m["exit_price_diff"] is not None]),
        "pnl_diff_yen100": _pctiles(pnl_abs),
        "actual_total_pnl_yen100": round(ap, 2),
        "replay_total_pnl_yen100": round(rp, 2),
        "total_pnl_abs_pct": round(total_pnl_pct, 4) if total_pnl_pct is not None else None,
        "gates": gates,
        "gate_ok": gate_ok,
        "verdict": "EXIT_BASELINE_REPRODUCED" if gate_ok else "EXIT_BASELINE_REPRODUCTION_BLOCKED",
        "matches": matches,
        "unmatched_actual": unmatched_actual,
        "note": "parity uses CurrentPrice X0 proxy + actual board_dynamic trail params; position_id identity match",
    }
