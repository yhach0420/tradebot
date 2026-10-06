"""Shadow collector for one later cash session.

Missing push is OBSERVATION_FAILED. It is not an observed zero-signal day.
The named unobserved dates stay NOT_OBSERVED and are never given signal counts.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.anchor_vs_event_driven.run_comparison import find_capture_dir
from research.pb1_v4_prospective_semantic_validation_preflight.eligibility import tse_cash_calendar
from research.symbol_setup_exit_causal_evidence import (
    CLASS_NOT_OBSERVED,
    CLASS_NOT_YET,
    CLASS_OBSERVATION_FAILED,
    CLASS_OBSERVED,
    CLASS_OBSERVED_ZERO,
    HISTORICAL_LAST_DATE,
    NOT_OBSERVED_DATES,
)
from research.symbol_setup_exit_causal_evidence.isolation import NATIVE

if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from _p1_inventory import resolve_universe  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
_NOT_OBSERVED = frozenset(NOT_OBSERVED_DATES)


def push_capture_present(day: str) -> bool:
    return find_capture_dir(str(day)) is not None


def _session_closed(day: str, now: datetime) -> bool:
    from research.anchor_timing_robustness.grid import hm_epoch
    from small_paper.v1r_live_dual_lane import session_end_for_position

    start = float(hm_epoch(day, 9, 0))
    end = float(session_end_for_position(date=day, session="AM", fill_time=start + 60.0))
    return float(now.timestamp()) > end


def _at(seq: Any, index: int) -> Any:
    if not isinstance(seq, list) or len(seq) <= index:
        return None
    return seq[index]


def classify_day(day: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Admission only. No signal counts are attached when the day was not scanned."""
    stamp = now or datetime.now(JST)
    key = str(day)
    cal = tse_cash_calendar(key)
    base = {
        "date": key,
        "tse_cash_session": bool(cal["is_tse_cash_session"]),
        "calendar_label": cal["label"],
        "population": "PROSPECTIVE_EXIT_EVIDENCE",
        "counts_as_evidence_day": False,
        "admitted": False,
        "push_capture_present": False,
    }
    if key <= HISTORICAL_LAST_DATE:
        return {**base, "classification": "HISTORICAL_DEVELOPMENT"}
    if key in _NOT_OBSERVED:
        return {**base, "classification": CLASS_NOT_OBSERVED, "reason": "push_capture_present=false"}
    if not cal["is_tse_cash_session"]:
        return {**base, "classification": "NOT_A_CASH_SESSION"}
    present = push_capture_present(key)
    if present:
        return {
            **base,
            "classification": CLASS_OBSERVED,
            "admitted": True,
            "push_capture_present": True,
            "counts_as_evidence_day": True,
        }
    if not _session_closed(key, stamp):
        return {**base, "classification": CLASS_NOT_YET, "reason": "session_not_closed"}
    return {**base, "classification": CLASS_OBSERVATION_FAILED, "reason": "push_capture_present=false"}


def _loss_row(episode: dict[str, Any], *, fill_t: Optional[float]) -> dict[str, Any]:
    return {
        "date": episode.get("date"),
        "symbol": episode.get("symbol"),
        "signal_t": episode.get("signal_t"),
        "fill_t": fill_t,
        "loss_t": episode.get("loss_t"),
        "loss_reason": episode.get("loss_reason"),
        "EMA9": episode.get("ema9"),
        "EMA21": episode.get("ema21"),
        "EMA21_t_minus_3": episode.get("ema21_lag3"),
        "EMA9_minus_EMA21": episode.get("fast_slow_gap_yen"),
        "EMA21_t_minus_EMA21_t_minus_3": episode.get("slow_slope_3bar_yen"),
        "loss_bid": episode.get("loss_bid0"),
        "population": episode.get("population"),
    }


def _diagnostic(path: dict[str, Any]) -> dict[str, Any]:
    thesis = path.get("forward_thesis_live")
    fast = path.get("forward_fast_slow_live")
    slow = path.get("forward_slow_rising")
    return {
        "role": "DIAGNOSTIC_ONLY",
        "changes_reference_exit": False,
        "strategy_pnl": False,
        "plus_1bar": {
            "thesis_live": _at(thesis, 0),
            "fast_slow_live": _at(fast, 0),
            "slow_trend_rising": _at(slow, 0),
            "causal_bid_bps": path.get("bid_plus_1bar_bps"),
        },
        "plus_2bar": {
            "thesis_live": _at(thesis, 1),
            "fast_slow_live": _at(fast, 1),
            "slow_trend_rising": _at(slow, 1),
            "causal_bid_bps": path.get("bid_plus_2bar_bps"),
        },
        "plus_3bar": {
            "thesis_live": _at(thesis, 2),
            "fast_slow_live": _at(fast, 2),
            "slow_trend_rising": _at(slow, 2),
            "causal_bid_bps": path.get("bid_plus_3bar_bps"),
        },
        "plus_60s_bid_bps": path.get("bid_60s_bps"),
        "plus_180s_bid_bps": path.get("bid_180s_bps"),
        "plus_300s_bid_bps": path.get("bid_300s_bps"),
        "classification": path.get("classification"),
    }


def shape_scan(day: str, scanned: dict[str, Any]) -> dict[str, Any]:
    """Split board-pass state paths from exact W5 fills. Zero after a real scan is observed."""
    episodes = []
    losses = []
    recovery = []
    for row in scanned["episodes"]:
        item = dict(row)
        item["population"] = "BOARD_PASS_STATE_EPISODES"
        episodes.append(item)
        if item.get("loss_reason"):
            losses.append(_loss_row(item, fill_t=None))
        if item.get("slope_only"):
            recovery.append(
                {
                    "date": item.get("date"),
                    "symbol": item.get("symbol"),
                    "population": "BOARD_PASS_STATE_EPISODES",
                    "classification": item.get("classification"),
                }
            )
    fills = []
    session_close = []
    for fill in scanned["fills"]:
        path = dict(fill.get("path") or {})
        reason = fill.get("actual_exit_reason")
        deteriorated = fill.get("actual_pnl_yen") is not None and float(fill["actual_pnl_yen"]) < 0.0
        permissive = reason == "SESSION_FAIL_CLOSE" and deteriorated
        fills.append(
            {
                "date": fill["date"],
                "symbol": fill["symbol"],
                "population": "EXACT_W5_SHADOW_FILLS",
                "signal_t": fill.get("signal_t"),
                "fill_t": fill.get("fill_t"),
                "reference_exit": {
                    "exit": "REFERENCE_K1_EXIT" if reason != "SESSION_FAIL_CLOSE" else "SESSION_FAIL_CLOSE",
                    "t": fill.get("actual_exit_time"),
                    "price": fill.get("actual_exit_price"),
                    "reason": reason,
                },
                "post_exit_diagnostic_path": _diagnostic(path),
                "classification": path.get("classification"),
                "session_fail_close": reason == "SESSION_FAIL_CLOSE",
                "THESIS_TOO_PERMISSIVE_CANDIDATE": permissive,
            }
        )
        if path.get("loss_reason"):
            losses.append(
                _loss_row(
                    {**path, "date": fill["date"], "symbol": fill["symbol"], "signal_t": fill.get("signal_t")},
                    fill_t=fill.get("fill_t"),
                )
            )
        if reason == "SESSION_FAIL_CLOSE":
            session_close.append(
                {
                    "date": fill["date"],
                    "symbol": fill["symbol"],
                    "exit_reason": "SESSION_FAIL_CLOSE",
                    "THESIS_TOO_PERMISSIVE_CANDIDATE": permissive,
                    "reference_exit_separate": True,
                }
            )
    slope = [row for row in episodes if row.get("slope_only")]
    filled_slope = [row for row in fills if row["reference_exit"]["reason"] == "SLOW_TREND_SLOPE_LOSS"]

    def _filled_flag(name: str) -> int:
        return sum(1 for row in filled_slope if (row.get("post_exit_diagnostic_path") or {}).get("classification") == name)

    technical = int(scanned["technical"])
    board = int(scanned["board"])
    fill_n = int(scanned["fill"])
    observed_zero = technical == 0 and board == 0 and fill_n == 0
    return {
        "date": day,
        "classification": CLASS_OBSERVED_ZERO if observed_zero else CLASS_OBSERVED,
        "admitted": True,
        "push_capture_present": True,
        "counts_as_evidence_day": True,
        "technical_signals": technical,
        "board_passes": board,
        "pending_n": 0,
        "exact_w5_shadow_fills": fill_n,
        "state_episodes": episodes,
        "shadow_fills": fills,
        "loss_reasons": losses,
        "recovery": recovery,
        "session_close": session_close,
        "new": {
            "slope_state": len(slope),
            "fast_slow": sum(1 for row in episodes if row.get("loss_reason") == "FAST_SLOW_CROSS_LOSS"),
            "both": sum(1 for row in episodes if row.get("loss_reason") == "BOTH_TREND_COMPONENTS_LOST"),
            "filled_slope": len(filled_slope),
            "temporary": sum(1 for row in slope if row.get("temporary_slope_interruption")),
            "price_recovered": sum(1 for row in slope if row.get("temporary_and_price_recovered")),
            "terminal": sum(1 for row in slope if row.get("classification") == "TERMINAL_SLOPE_FAILURE"),
            "ambiguous": sum(1 for row in slope if row.get("classification") == "AMBIGUOUS_SLOPE_FAILURE"),
            "filled_temporary": sum(1 for row in filled_slope if (row.get("classification") in {"TEMPORARY_SLOPE_INTERRUPTION", "TEMPORARY_AND_PRICE_RECOVERED"})),
            "filled_price_recovered": _filled_flag("TEMPORARY_AND_PRICE_RECOVERED"),
            "filled_terminal": _filled_flag("TERMINAL_SLOPE_FAILURE"),
            "filled_ambiguous": _filled_flag("AMBIGUOUS_SLOPE_FAILURE"),
            "session_close_permissive": sum(1 for row in session_close if row["THESIS_TOO_PERMISSIVE_CANDIDATE"]),
        },
    }


def collect_day(day: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Scan only an admitted session. A failed observation carries no signal counts."""
    status = classify_day(day, now=now)
    if not status["admitted"]:
        return status
    cap = find_capture_dir(str(day))
    if cap is None:
        failed = dict(status)
        failed["admitted"] = False
        failed["counts_as_evidence_day"] = False
        failed["classification"] = CLASS_OBSERVATION_FAILED
        failed["push_capture_present"] = False
        return failed
    uni = resolve_universe(str(day), Path(cap))
    if not uni.get("resolved") or not list(uni.get("symbols") or []):
        failed = dict(status)
        failed["admitted"] = False
        failed["counts_as_evidence_day"] = False
        failed["classification"] = CLASS_OBSERVATION_FAILED
        failed["push_capture_present"] = True
        failed["reason"] = str(uni.get("reason") or "UNIVERSE_BINDING_UNRESOLVED")
        return failed
    from research.symbol_setup_exit_noise_rca.scan import _day as frozen_day

    scanned = frozen_day(str(day), Path(cap), [str(s) for s in uni["symbols"]])
    return shape_scan(str(day), scanned)
