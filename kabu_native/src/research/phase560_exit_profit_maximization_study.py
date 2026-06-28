"""
Phase560 — EXIT profit maximization study (research only).

Latest Runtime (Phase558) fixed. Analyzes MFE/exit efficiency, early profit-taking,
opportunity loss, trailing parameter shadows, exit failure taxonomy, and 6/18 vs 6/22.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import HARD_STOP_PCT, _max_drawdown_yen
from research.phase404_no_progress_exit_shadow import _exit_result, build_tick_states
from research.phase428_no_progress_tightening_sweep import tightening_matches
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase484_stop_low_mfe_feature_discovery import _load_day_event_snaps
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _latest_live_day,
)
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
    _resolved_exit_reason,
)
from research.phase546_entry_cluster_shadow_replay import _merge_dataset, _trade_key as _cluster_trade_key
from research.phase547_reject_cluster_winner_rescue import _period_thresholds
from research.phase551_current_runtime_full_period_replay import (
    E4_THRESHOLD,
    _is_or_trade,
    _iter_calendar_days,
)
from research.phase554_stop_low_mfe_entry_quality_feature_study import _enrich_phase554
from research.phase558_current_runtime_after_phase557 import (
    _evaluate_live_trades,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.board_dynamic_trailing_shadow import (
    BOARD_HIGH_ACTIVATE_PCT,
    BOARD_HIGH_GIVEBACK_FRAC,
    BOARD_LOW_ACTIVATE_PCT,
    BOARD_LOW_GIVEBACK_FRAC,
    board_tier_from_percentile,
    trailing_params_for_board_tier,
)
from replay.pnl_yen import compute_pnl_yen_100

PHASE560_VERDICT = "phase560_exit_profit_maximization_study_done"
LIVE_START = "20260616"
PERIOD_END_DEFAULT = "20260625"
EXTENDED_START = "20260529"
DAY_618 = "20260618"
DAY_622 = "20260622"

EARLY_RULES = (
    ("E1", 1.0, 0.4),
    ("E2", 1.5, 0.7),
    ("E3", 2.0, 1.0),
)

EFFICIENCY_FIELDS = [
    "trade_key",
    "symbol",
    "day",
    "entry_type",
    "entry_time",
    "exit_time",
    "hold_sec",
    "entry_price",
    "exit_price",
    "pnl_yen_100",
    "pnl_pct",
    "mfe_pct",
    "mae_pct",
    "mfe_time_sec",
    "mfe_capture_ratio",
    "giveback_pct",
    "opportunity_loss_pct",
    "exit_reason",
    "trailing_activated",
    "stop_hit",
    "session_close",
    "board_tier",
    "early_profit_take",
    "early_profit_rules",
]

EARLY_FIELDS = [
    "rule_id",
    "mfe_threshold_pct",
    "max_exit_pnl_pct",
    "trade_count",
    "total_pnl_yen_100",
    "avg_opportunity_loss_pct",
    "avg_giveback_pct",
    "exit_reason_top",
]

OPP_FIELDS = [
    "segment_type",
    "segment_value",
    "trade_count",
    "total_pnl_yen_100",
    "total_opportunity_loss_pct",
    "avg_opportunity_loss_pct",
    "avg_mfe_pct",
    "avg_realized_pnl_pct",
]

TRAILING_FIELDS = [
    "scenario_id",
    "label",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "avg_win_yen_100",
    "avg_loss_yen_100",
    "mfe_capture_ratio",
    "stop_hit_count",
    "trailing_exit_count",
    "session_close_count",
    "no_progress_count",
    "opportunity_loss_total_pct",
    "delta_pnl_vs_t0",
    "delta_pf_vs_t0",
    "delta_maxdd_vs_t0",
    "runtime_candidate",
]

CLASS_FIELDS = [
    "trade_key",
    "symbol",
    "day",
    "exit_reason",
    "pnl_yen_100",
    "mfe_pct",
    "giveback_pct",
    "opportunity_loss_pct",
    "exit_classification",
    "notes",
]

DAY_COMPARE_FIELDS = [
    "day",
    "trades",
    "pnl_yen_100",
    "avg_mfe_pct",
    "avg_realized_pnl_pct",
    "avg_giveback_pct",
    "avg_opportunity_loss_pct",
    "trailing_exit_rate",
    "stop_hit_rate",
    "session_close_rate",
    "mfe_capture_ratio",
    "early_profit_take_count",
]


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _pnl_pct(entry_px: float, px: float) -> float:
    if entry_px <= 0:
        return 0.0
    return round((px - entry_px) / entry_px * 100.0, 6)


def _entry_type_label(trade: Mapping[str, Any]) -> str:
    return "OR" if _is_or_trade(trade) else "PBV2"


@dataclass(frozen=True)
class TrailingShadowSpec:
    scenario_id: str
    label: str
    activate_delta: float = 0.0
    giveback_delta: float = 0.0
    board_high_activate: Optional[float] = None
    board_high_giveback: Optional[float] = None
    board_low_activate: Optional[float] = None
    board_low_giveback: Optional[float] = None
    profit_protect: bool = False
    profit_protect_mfe: float = 1.0
    profit_protect_floor: float = 0.3


TRAILING_SPECS: tuple[TrailingShadowSpec, ...] = (
    TrailingShadowSpec("T0", "current board dynamic trailing"),
    TrailingShadowSpec("T1", "slower trailing (+0.2% activate, +10pt giveback)", activate_delta=0.2, giveback_delta=0.10),
    TrailingShadowSpec("T2", "faster trailing (-0.2% activate, -10pt giveback)", activate_delta=-0.2, giveback_delta=-0.10),
    TrailingShadowSpec("T3", "board_high loosen 1.2%/70%", board_high_activate=1.2, board_high_giveback=0.70),
    TrailingShadowSpec("T4", "board_low tighter 0.5%/30%", board_low_activate=0.5, board_low_giveback=0.30),
    TrailingShadowSpec("T5", "profit_protect floor +0.3% after MFE>=1.0%", profit_protect=True),
    TrailingShadowSpec(
        "T6",
        "hybrid board_high loosen + board_low tighter",
        board_high_activate=1.2,
        board_high_giveback=0.70,
        board_low_activate=0.5,
        board_low_giveback=0.30,
    ),
)


def _trailing_params(
    imb_pct: Optional[float],
    spec: TrailingShadowSpec,
) -> tuple[float, float, str]:
    tier = board_tier_from_percentile(imb_pct)
    if tier == "board_high":
        act = (
            spec.board_high_activate
            if spec.board_high_activate is not None
            else BOARD_HIGH_ACTIVATE_PCT + spec.activate_delta
        )
        gb = (
            spec.board_high_giveback
            if spec.board_high_giveback is not None
            else BOARD_HIGH_GIVEBACK_FRAC + spec.giveback_delta
        )
    else:
        act = (
            spec.board_low_activate
            if spec.board_low_activate is not None
            else BOARD_LOW_ACTIVATE_PCT + spec.activate_delta
        )
        gb = (
            spec.board_low_giveback
            if spec.board_low_giveback is not None
            else BOARD_LOW_GIVEBACK_FRAC + spec.giveback_delta
        )
    return max(0.1, act), min(max(gb, 0.05), 0.95), tier


def simulate_trailing_shadow_exit(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    imb_pct: Optional[float],
    spec: TrailingShadowSpec,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")

    profit_armed = False
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        peak_mfe = float(state["peak_mfe"])

        if spec.profit_protect and peak_mfe >= spec.profit_protect_mfe:
            profit_armed = True
        if profit_armed and pnl <= spec.profit_protect_floor:
            return _exit_result(entry_price, px, ts, pnl, "profit_protect_exit")

        if tightening_matches(state, BEST_NP_POLICY):
            return _exit_result(entry_price, px, ts, pnl, "no_progress_exit")

        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")

        activate, giveback_frac, _ = _trailing_params(imb_pct, spec)
        if peak_mfe >= activate and pnl <= peak_mfe * giveback_frac:
            return _exit_result(entry_price, px, ts, pnl, "trailing_mfe_exit")

    last = states[-1]
    return _exit_result(
        entry_price,
        float(last["px"]),
        float(last["ts"]),
        float(last["pnl"]),
        "session_close",
    )


def _series_from_index(
    price_idx: Mapping[tuple[str, str], list[tuple[Any, float]]],
    symbol: str,
    day: str,
) -> list[tuple[float, float]]:
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    raw = price_idx.get((sym, day), [])
    return [(ts.timestamp(), px) for ts, px in raw if px > 0]


def _stream_states(
    trade: Mapping[str, Any],
    series: Sequence[tuple[float, float]],
) -> Optional[tuple[list[dict[str, Any]], float, float, Optional[float]]]:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    entry_px = _float(trade.get("entry_price"))
    if ent is None or not entry_px or entry_px <= 0 or len(series) < 3:
        return None
    ent_ts = ent.timestamp()
    forward = [(ts, px) for ts, px in series if ts >= ent_ts - 1.0 and px > 0]
    if len(forward) < 3:
        return None
    session_end = float(forward[-1][0])
    vwap_dev = _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))
    states = build_tick_states(
        forward,
        entry_ts=ent_ts,
        entry_price=entry_px,
        session_end_ts=session_end,
        entry_vwap_dev_pct=vwap_dev,
    )
    if not states:
        return None
    imb = _float(trade.get("entry_imbalance_percentile"))
    return states, entry_px, ent_ts, imb


def _path_metrics(
    trade: Mapping[str, Any],
    states: Sequence[Mapping[str, Any]],
    *,
    entry_px: float,
    ent_ts: float,
    imb_pct: Optional[float],
) -> dict[str, Any]:
    peak_mfe = max((float(s["peak_mfe"]) for s in states), default=_mfe_pct(trade))
    mae = min((float(s["pnl"]) for s in states), default=0.0)
    mfe_time = 0.0
    running_peak = 0.0
    for s in states:
        p = float(s["pnl"])
        if p > running_peak + 1e-9:
            running_peak = p
            if abs(p - peak_mfe) < 1e-6 or p >= peak_mfe - 1e-6:
                mfe_time = float(s["elapsed"])
                break
    exit_px = _float(trade.get("exit_price")) or entry_px
    realized = _pnl_pct(entry_px, exit_px)
    if trade.get("pnl_yen_100") is not None and entry_px > 0:
        realized = round(_num(trade.get("pnl_yen_100")) / (entry_px * 100.0) * 100.0, 6)
    giveback = round(max(0.0, peak_mfe - realized), 6)
    opp = round(max(0.0, peak_mfe - realized), 6)
    capture = round(realized / peak_mfe, 4) if peak_mfe > 0 else None
    activate, _, tier = trailing_params_for_board_tier(imb_pct)
    reason = normalize_exit_reason(_resolved_exit_reason(trade))
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    ext = _parse_ts(str(trade.get("exit_time") or ""))
    hold = (ext - ent).total_seconds() if ent and ext else _num(trade.get("hold_sec"))
    early_rules: list[str] = []
    for rid, mfe_thr, pnl_max in EARLY_RULES:
        if peak_mfe >= mfe_thr and realized < pnl_max:
            early_rules.append(rid)
    return {
        "peak_mfe": round(peak_mfe, 4),
        "mae_pct": round(mae, 4),
        "mfe_time_sec": round(mfe_time, 2),
        "realized_pnl_pct": round(realized, 6),
        "giveback_pct": giveback,
        "opportunity_loss_pct": opp,
        "mfe_capture_ratio": capture,
        "board_tier": tier,
        "trailing_activated": peak_mfe >= activate,
        "stop_hit": reason == "stop_hit",
        "session_close": reason == "session_close",
        "exit_reason": reason,
        "hold_sec": round(hold, 2),
        "early_profit_rules": "|".join(early_rules),
        "early_profit_take": bool(early_rules),
    }


def _classify_exit(row: Mapping[str, Any]) -> tuple[str, str]:
    reason = str(row.get("exit_reason") or "")
    pnl = _num(row.get("pnl_yen_100"))
    mfe = _num(row.get("mfe_pct"))
    giveback = _num(row.get("giveback_pct"))
    opp = _num(row.get("opportunity_loss_pct"))
    capture = row.get("mfe_capture_ratio")
    cap_f = float(capture) if capture is not None else 0.0

    if row.get("early_profit_take"):
        return "Too Early", "MFE threshold exceeded but exit PnL below rule"
    if reason == "stop_hit":
        if mfe >= 0.8:
            return "Stop Too Wide", f"stop after MFE={mfe}"
        return "Stop Appropriate", f"stop with MFE={mfe}"
    if reason == "session_close" and giveback >= 0.5 and mfe >= 0.6:
        return "Session Close Risk", f"giveback={giveback}"
    if reason in ("trailing_mfe", "trailing") and opp >= 0.8 and pnl > 0:
        return "Trailing Too Tight", f"left {opp} on table"
    if reason in ("trailing_mfe", "trailing") and cap_f >= 0.55 and pnl > 0:
        return "Good Exit", f"capture={cap_f}"
    if reason in ("trailing_mfe", "trailing") and opp >= 1.0 and pnl <= 0:
        return "Trailing Too Loose", "trailed into loss after high MFE"
    if pnl < 0 and mfe >= 1.0:
        return "Too Late", f"loss after MFE={mfe}"
    if pnl > 0 and cap_f >= 0.45:
        return "Good Exit", f"capture={cap_f}"
    if pnl < 0:
        return "Too Late", reason
    return "Good Exit", reason


def _load_phase558_accepted(repo: Path, *, live_start: str, end: str) -> list[dict[str, Any]]:
    reports = resolve_reports_dir(repo)
    kabu = resolve_kabu_root(repo)
    cluster_rows = _merge_dataset(reports)
    cluster_by_key = {_cluster_trade_key(r): dict(r) for r in cluster_rows}
    thresholds = _period_thresholds(cluster_rows)
    thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

    days = [d for d in _iter_calendar_days(live_start, end) if d >= PERIOD_START_LIVE]
    live_trades: list[dict[str, Any]] = []
    for day in days:
        for t in _load_canonical_trades_for_day(repo, day, all_sessions=True):
            key = _cluster_trade_key(t)
            merged = {**dict(t), **cluster_by_key.get(key, {})}
            merged["day"] = day
            live_trades.append(merged)

    symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
    price_idx = _build_price_index_to(kabu, period_end=end)
    bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)
    from research.phase518_day_high_winner_loser_separation import _build_micro_lookup

    micro = _build_micro_lookup(live_trades)
    board_snaps = {day: _load_day_event_snaps(kabu, day) for day in days}
    enriched = _enrich_phase554(
        live_trades,
        bar_cache=bar_cache,
        micro_lookup=micro,
        board_snaps_by_day=board_snaps,
    )
    ev = _evaluate_live_trades(
        enriched,
        include_or=True,
        reentry_rsi=True,
        entry_quality=True,
        cluster_guard=True,
        cluster_exception=True,
        stop_low_mfe_guard=True,
        bar_cache=bar_cache,
        thresholds=thresholds,
        missing_policy="pass",
    )
    return list(ev.get("_accepted") or [])


def _shadow_metrics(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = sorted(outcomes, key=lambda r: str(r.get("exit_time") or r.get("entry_time") or ""))
    pnls = [_num(r.get("pnl_yen_100")) for r in chron]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    opps = [_num(r.get("opportunity_loss_pct")) for r in outcomes]
    captures = [
        float(r["mfe_capture_ratio"])
        for r in outcomes
        if r.get("mfe_capture_ratio") is not None and _num(r.get("mfe_pct")) > 0
    ]
    reasons = Counter(str(r.get("shadow_exit_reason") or r.get("exit_reason") or "") for r in outcomes)
    out = {
        "trades": len(outcomes),
        "pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(pnls), 2),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "avg_win_yen_100": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss_yen_100": round(statistics.mean(losses), 2) if losses else 0.0,
        "mfe_capture_ratio": round(statistics.mean(captures), 4) if captures else 0.0,
        "stop_hit_count": reasons.get("stop_hit", 0),
        "trailing_exit_count": reasons.get("trailing_mfe_exit", 0) + reasons.get("trailing_mfe", 0),
        "session_close_count": reasons.get("session_close", 0),
        "no_progress_count": reasons.get("no_progress_exit", 0),
        "opportunity_loss_total_pct": round(sum(opps), 4),
    }
    if baseline:
        out["delta_pnl_vs_t0"] = round(out["pnl_yen_100"] - _num(baseline.get("pnl_yen_100")), 2)
        out["delta_pf_vs_t0"] = round(_num(out["profit_factor"]) - _num(baseline.get("profit_factor")), 4)
        out["delta_maxdd_vs_t0"] = round(
            _num(out["max_drawdown_yen_100"]) - _num(baseline.get("max_drawdown_yen_100")), 2
        )
    return out


def _runtime_candidate_row(row: Mapping[str, Any], baseline: Mapping[str, Any]) -> bool:
    return (
        _num(row.get("delta_pnl_vs_t0")) > 0
        and _num(row.get("profit_factor")) >= _num(baseline.get("profit_factor"))
        and _num(row.get("max_drawdown_yen_100")) <= _num(baseline.get("max_drawdown_yen_100")) + 5000
        and _num(row.get("opportunity_loss_total_pct")) <= _num(baseline.get("opportunity_loss_total_pct"))
        and _num(row.get("stop_hit_count")) <= _num(baseline.get("stop_hit_count")) + 5
        and _num(row.get("win_rate")) >= _num(baseline.get("win_rate")) - 0.05
    )


def _mandatory_answers(
    *,
    efficiency: Sequence[Mapping[str, Any]],
    early_rows: Sequence[Mapping[str, Any]],
    opp_rows: Sequence[Mapping[str, Any]],
    trailing_rows: Sequence[Mapping[str, Any]],
    day_rows: Sequence[Mapping[str, Any]],
    classifications: Counter[str],
) -> dict[str, Any]:
    total = len(efficiency) or 1
    early_count = sum(1 for r in efficiency if r.get("early_profit_take"))
    avg_opp = statistics.mean([_num(r.get("opportunity_loss_pct")) for r in efficiency]) if efficiency else 0.0
    by_reason = [r for r in opp_rows if r.get("segment_type") == "exit_reason"]
    worst_reason = max(by_reason, key=lambda r: _num(r.get("total_opportunity_loss_pct")), default={})
    t0 = next((r for r in trailing_rows if r.get("scenario_id") == "T0"), {})
    best = max(
        trailing_rows,
        key=lambda r: (_num(r.get("delta_pnl_vs_t0")), _num(r.get("profit_factor"))),
    )
    d618 = next((r for r in day_rows if r.get("day") == DAY_618), {})
    d622 = next((r for r in day_rows if r.get("day") == DAY_622), {})

    tight_score = sum(
        _num(r.get("total_opportunity_loss_pct"))
        for r in trailing_rows
        if r.get("scenario_id") in ("T2", "T4")
    )
    loose_score = sum(
        _num(r.get("delta_pnl_vs_t0"))
        for r in trailing_rows
        if r.get("scenario_id") in ("T1", "T3", "T6")
    )

    bh_delta = _num(
        next((r.get("delta_pnl_vs_t0") for r in trailing_rows if r.get("scenario_id") == "T3"), 0)
    )
    bl_delta = _num(
        next((r.get("delta_pnl_vs_t0") for r in trailing_rows if r.get("scenario_id") == "T4"), 0)
    )

    candidates = [r for r in trailing_rows if r.get("runtime_candidate")]

    return {
        "1_early_profit_take_common": early_count / total >= 0.15,
        "1_early_profit_take_rate": round(early_count / total, 4),
        "1_early_profit_take_count": early_count,
        "2_opportunity_loss_large": avg_opp >= 0.35,
        "2_avg_opportunity_loss_pct": round(avg_opp, 4),
        "3_worst_exit_reason": worst_reason.get("segment_value"),
        "3_worst_exit_reason_opp": worst_reason.get("total_opportunity_loss_pct"),
        "4_trailing_too_tight": classifications.get("Trailing Too Tight", 0) >= 5,
        "5_trailing_too_loose": classifications.get("Trailing Too Loose", 0) >= 3,
        "6_board_improvement": "board_high" if bh_delta >= bl_delta else "board_low",
        "6_board_high_t3_delta": bh_delta,
        "6_board_low_t4_delta": bl_delta,
        "7_day618_exit_improvable": _num(d618.get("avg_opportunity_loss_pct")) > 0.4,
        "7_day618_pnl": d618.get("pnl_yen_100"),
        "8_day622_more_extendable": _num(d622.get("avg_opportunity_loss_pct")) > 0.3,
        "8_day622_pnl": d622.get("pnl_yen_100"),
        "9_best_shadow": best.get("scenario_id"),
        "9_best_shadow_delta_pnl": best.get("delta_pnl_vs_t0"),
        "10_runtime_candidates": [r.get("scenario_id") for r in candidates],
        "10_has_runtime_candidate": bool(candidates),
        "11_next_phase": (
            "phase561_trailing_shadow_validation"
            if candidates
            else "phase561_exit_observability_and_live_capture_audit"
        ),
        "classification_counts": dict(classifications),
        "early_rule_summary": list(early_rows),
    }


@dataclass
class Phase560Job:
    repo_root: Path
    live_start: str = LIVE_START
    live_end: str = PERIOD_END_DEFAULT

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        end = min(self.live_end, _latest_live_day(repo))
        accepted = _load_phase558_accepted(repo, live_start=self.live_start, end=end)
        if not accepted:
            raise RuntimeError("No Phase558 accepted trades for Phase560")

        price_idx = _build_price_index_to(kabu, period_end=end)

        efficiency_rows: list[dict[str, Any]] = []
        classification_rows: list[dict[str, Any]] = []
        class_counter: Counter[str] = Counter()
        shadow_by_spec: dict[str, list[dict[str, Any]]] = {s.scenario_id: [] for s in TRAILING_SPECS}

        for trade in accepted:
            sym = str(trade.get("symbol") or "")
            day = str(trade.get("day") or "")[:8]
            series = _series_from_index(price_idx, sym, day)
            streamed = _stream_states(trade, series)
            entry_px = _float(trade.get("entry_price")) or 0.0

            if streamed is None:
                peak = round(_mfe_pct(trade), 4)
                realized = round(_num(trade.get("pnl_yen_100")) / max(entry_px * 100.0, 1) * 100.0, 6)
                path = {
                    "peak_mfe": peak,
                    "mae_pct": round(_float(trade.get("mae_pct")) or 0.0, 4),
                    "mfe_time_sec": None,
                    "realized_pnl_pct": realized,
                    "giveback_pct": round(max(0.0, peak - realized), 6),
                    "opportunity_loss_pct": round(max(0.0, peak - realized), 6),
                    "mfe_capture_ratio": round(realized / peak, 4) if peak > 0 else None,
                    "board_tier": board_tier_from_percentile(_float(trade.get("entry_imbalance_percentile"))),
                    "trailing_activated": False,
                    "stop_hit": normalize_exit_reason(_resolved_exit_reason(trade)) == "stop_hit",
                    "session_close": normalize_exit_reason(_resolved_exit_reason(trade)) == "session_close",
                    "exit_reason": normalize_exit_reason(_resolved_exit_reason(trade)),
                    "hold_sec": _num(trade.get("hold_sec")),
                    "early_profit_rules": "",
                    "early_profit_take": False,
                }
                states = []
                ent_ts = (_parse_ts(str(trade.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)).timestamp()
                imb = _float(trade.get("entry_imbalance_percentile"))
            else:
                states, entry_px, ent_ts, imb = streamed
                path = _path_metrics(trade, states, entry_px=entry_px, ent_ts=ent_ts, imb_pct=imb)

            row = {
                "trade_key": "|".join(_cluster_trade_key(trade)),
                "symbol": sym.replace(".T", ""),
                "day": day,
                "entry_type": _entry_type_label(trade),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "hold_sec": path["hold_sec"],
                "entry_price": entry_px,
                "exit_price": _float(trade.get("exit_price")),
                "pnl_yen_100": round(_num(trade.get("pnl_yen_100")), 2),
                "pnl_pct": path["realized_pnl_pct"],
                "mfe_pct": path["peak_mfe"],
                "mae_pct": path["mae_pct"],
                "mfe_time_sec": path["mfe_time_sec"],
                "mfe_capture_ratio": path["mfe_capture_ratio"],
                "giveback_pct": path["giveback_pct"],
                "opportunity_loss_pct": path["opportunity_loss_pct"],
                "exit_reason": path["exit_reason"],
                "trailing_activated": path["trailing_activated"],
                "stop_hit": path["stop_hit"],
                "session_close": path["session_close"],
                "board_tier": path["board_tier"],
                "early_profit_take": path["early_profit_take"],
                "early_profit_rules": path["early_profit_rules"],
            }
            efficiency_rows.append(row)

            cls, note = _classify_exit(row)
            class_counter[cls] += 1
            classification_rows.append(
                {
                    **{k: row[k] for k in ("trade_key", "symbol", "day", "exit_reason", "pnl_yen_100", "mfe_pct", "giveback_pct", "opportunity_loss_pct")},
                    "exit_classification": cls,
                    "notes": note,
                }
            )

            if states:
                for spec in TRAILING_SPECS:
                    sim = simulate_trailing_shadow_exit(
                        states,
                        entry_price=entry_px,
                        entry_ts=ent_ts,
                        imb_pct=imb,
                        spec=spec,
                    )
                    exit_px = float(sim.get("shadow_exit_price") or entry_px)
                    pnl_yen = float(sim.get("shadow_pnl_yen_100") or compute_pnl_yen_100(entry_px, exit_px))
                    peak_mfe = max(float(s["peak_mfe"]) for s in states)
                    realized = float(sim.get("shadow_pnl_pct") or _pnl_pct(entry_px, exit_px))
                    shadow_by_spec[spec.scenario_id].append(
                        {
                            "trade_key": row["trade_key"],
                            "entry_time": row["entry_time"],
                            "exit_time": datetime.fromtimestamp(
                                float(sim.get("shadow_exit_ts") or ent_ts), tz=JST
                            ).isoformat()
                            if float(sim.get("shadow_exit_ts") or 0) > 0
                            else "",
                            "pnl_yen_100": round(pnl_yen, 2),
                            "mfe_pct": round(peak_mfe, 4),
                            "opportunity_loss_pct": round(max(0.0, peak_mfe - realized), 6),
                            "mfe_capture_ratio": round(realized / peak_mfe, 4) if peak_mfe > 0 else None,
                            "shadow_exit_reason": normalize_exit_reason(str(sim.get("shadow_exit_reason") or "")),
                            "exit_reason": normalize_exit_reason(str(sim.get("shadow_exit_reason") or "")),
                        }
                    )

        early_rows: list[dict[str, Any]] = []
        for rid, mfe_thr, pnl_max in EARLY_RULES:
            bucket = [r for r in efficiency_rows if _num(r.get("mfe_pct")) >= mfe_thr and _num(r.get("pnl_pct")) < pnl_max]
            reasons = Counter(str(r.get("exit_reason") or "") for r in bucket)
            early_rows.append(
                {
                    "rule_id": rid,
                    "mfe_threshold_pct": mfe_thr,
                    "max_exit_pnl_pct": pnl_max,
                    "trade_count": len(bucket),
                    "total_pnl_yen_100": round(sum(_num(r.get("pnl_yen_100")) for r in bucket), 2),
                    "avg_opportunity_loss_pct": round(
                        statistics.mean([_num(r.get("opportunity_loss_pct")) for r in bucket]), 4
                    )
                    if bucket
                    else 0.0,
                    "avg_giveback_pct": round(statistics.mean([_num(r.get("giveback_pct")) for r in bucket]), 4)
                    if bucket
                    else 0.0,
                    "exit_reason_top": reasons.most_common(1)[0][0] if reasons else "",
                }
            )

        opp_rows: list[dict[str, Any]] = []

        def _opp_segment(segment_type: str, key_fn: Callable[[Mapping[str, Any]], str]) -> None:
            grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for r in efficiency_rows:
                grouped[key_fn(r)].append(r)
            for val, rows in sorted(grouped.items()):
                opp_rows.append(
                    {
                        "segment_type": segment_type,
                        "segment_value": val,
                        "trade_count": len(rows),
                        "total_pnl_yen_100": round(sum(_num(r.get("pnl_yen_100")) for r in rows), 2),
                        "total_opportunity_loss_pct": round(
                            sum(_num(r.get("opportunity_loss_pct")) for r in rows), 4
                        ),
                        "avg_opportunity_loss_pct": round(
                            statistics.mean([_num(r.get("opportunity_loss_pct")) for r in rows]), 4
                        )
                        if rows
                        else 0.0,
                        "avg_mfe_pct": round(statistics.mean([_num(r.get("mfe_pct")) for r in rows]), 4)
                        if rows
                        else 0.0,
                        "avg_realized_pnl_pct": round(statistics.mean([_num(r.get("pnl_pct")) for r in rows]), 4)
                        if rows
                        else 0.0,
                    }
                )

        _opp_segment("all", lambda _r: "all")
        _opp_segment("exit_reason", lambda r: str(r.get("exit_reason") or ""))
        _opp_segment("entry_type", lambda r: str(r.get("entry_type") or ""))
        _opp_segment("symbol", lambda r: str(r.get("symbol") or ""))

        top10 = sorted(efficiency_rows, key=lambda r: _num(r.get("opportunity_loss_pct")), reverse=True)[:10]
        for i, r in enumerate(top10, 1):
            opp_rows.append(
                {
                    "segment_type": "top10",
                    "segment_value": str(i),
                    "trade_count": 1,
                    "total_pnl_yen_100": r.get("pnl_yen_100"),
                    "total_opportunity_loss_pct": r.get("opportunity_loss_pct"),
                    "avg_opportunity_loss_pct": r.get("opportunity_loss_pct"),
                    "avg_mfe_pct": r.get("mfe_pct"),
                    "avg_realized_pnl_pct": r.get("pnl_pct"),
                }
            )

        trailing_rows: list[dict[str, Any]] = []
        baseline_metrics: dict[str, Any] = {}
        for spec in TRAILING_SPECS:
            raw_metrics = _shadow_metrics(shadow_by_spec[spec.scenario_id])
            if spec.scenario_id == "T0":
                baseline_metrics = raw_metrics
                metrics = {**raw_metrics, "delta_pnl_vs_t0": 0.0, "delta_pf_vs_t0": 0.0, "delta_maxdd_vs_t0": 0.0}
            else:
                metrics = _shadow_metrics(shadow_by_spec[spec.scenario_id], baseline=baseline_metrics)
            trailing_rows.append(
                {
                    "scenario_id": spec.scenario_id,
                    "label": spec.label,
                    **metrics,
                    "runtime_candidate": (
                        spec.scenario_id != "T0" and _runtime_candidate_row(metrics, baseline_metrics)
                    ),
                }
            )

        day_rows: list[dict[str, Any]] = []
        for day in sorted({str(r.get("day") or "") for r in efficiency_rows}):
            rows = [r for r in efficiency_rows if r.get("day") == day]
            n = len(rows) or 1
            day_rows.append(
                {
                    "day": day,
                    "trades": len(rows),
                    "pnl_yen_100": round(sum(_num(r.get("pnl_yen_100")) for r in rows), 2),
                    "avg_mfe_pct": round(statistics.mean([_num(r.get("mfe_pct")) for r in rows]), 4),
                    "avg_realized_pnl_pct": round(statistics.mean([_num(r.get("pnl_pct")) for r in rows]), 4),
                    "avg_giveback_pct": round(statistics.mean([_num(r.get("giveback_pct")) for r in rows]), 4),
                    "avg_opportunity_loss_pct": round(
                        statistics.mean([_num(r.get("opportunity_loss_pct")) for r in rows]), 4
                    ),
                    "trailing_exit_rate": round(
                        sum(1 for r in rows if str(r.get("exit_reason") or "") in ("trailing_mfe", "trailing"))
                        / n,
                        4,
                    ),
                    "stop_hit_rate": round(sum(1 for r in rows if r.get("stop_hit")) / n, 4),
                    "session_close_rate": round(sum(1 for r in rows if r.get("session_close")) / n, 4),
                    "mfe_capture_ratio": round(
                        statistics.mean(
                            [
                                float(r["mfe_capture_ratio"])
                                for r in rows
                                if r.get("mfe_capture_ratio") is not None and _num(r.get("mfe_pct")) > 0
                            ]
                        ),
                        4,
                    )
                    if any(r.get("mfe_capture_ratio") is not None for r in rows)
                    else 0.0,
                    "early_profit_take_count": sum(1 for r in rows if r.get("early_profit_take")),
                }
            )

        compare_days = [r for r in day_rows if r.get("day") in (DAY_618, DAY_622)]

        answers = _mandatory_answers(
            efficiency=efficiency_rows,
            early_rows=early_rows,
            opp_rows=opp_rows,
            trailing_rows=trailing_rows,
            day_rows=compare_days,
            classifications=class_counter,
        )

        return {
            "verdict": PHASE560_VERDICT,
            "generated_at": _now_iso(),
            "period_live": f"{self.live_start}-{end}",
            "runtime": "Phase558 latest (OR+guards+ClusterGuard+SLM)",
            "accepted_trades": len(accepted),
            "efficiency_rows": efficiency_rows,
            "early_profit_take": early_rows,
            "opportunity_loss": opp_rows,
            "trailing_shadow": trailing_rows,
            "exit_classification": classification_rows,
            "day_compare": compare_days,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)
        docs = kabu / "docs" / "operations" / "phase560_exit_profit_maximization_study.md"

        paths = {
            "efficiency": reports / "phase560_exit_efficiency_trades.csv",
            "early": reports / "phase560_early_profit_take.csv",
            "opportunity": reports / "phase560_opportunity_loss.csv",
            "trailing": reports / "phase560_trailing_shadow_summary.csv",
            "classification": reports / "phase560_exit_failure_classification.csv",
            "day_compare": reports / "phase560_day_compare_0618_0622.csv",
            "report": reports / "phase560_report.json",
            "docs": docs,
        }
        _write_csv(paths["efficiency"], EFFICIENCY_FIELDS, result.get("efficiency_rows") or [])
        _write_csv(paths["early"], EARLY_FIELDS, result.get("early_profit_take") or [])
        _write_csv(paths["opportunity"], OPP_FIELDS, result.get("opportunity_loss") or [])
        _write_csv(paths["trailing"], TRAILING_FIELDS, result.get("trailing_shadow") or [])
        _write_csv(paths["classification"], CLASS_FIELDS, result.get("exit_classification") or [])
        _write_csv(paths["day_compare"], DAY_COMPARE_FIELDS, result.get("day_compare") or [])

        payload = {k: v for k, v in result.items() if k != "efficiency_rows" and k != "exit_classification"}
        paths["report"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        lines = [
            "# Phase560 — EXIT Profit Maximization Study",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Generated:** {result.get('generated_at')}",
            f"**Period:** {result.get('period_live')} (Phase558 accepted trades)",
            f"**Trades:** {result.get('accepted_trades')}",
            "",
            "## Mandatory answers",
            "",
            f"1. **Early profit-taking common?** {ma.get('1_early_profit_take_common')} "
            f"({ma.get('1_early_profit_take_count')} trades, rate={ma.get('1_early_profit_take_rate')})",
            f"2. **Opportunity loss large?** {ma.get('2_opportunity_loss_large')} "
            f"(avg={ma.get('2_avg_opportunity_loss_pct')} pct pts)",
            f"3. **Worst exit_reason:** {ma.get('3_worst_exit_reason')} "
            f"(opp={ma.get('3_worst_exit_reason_opp')})",
            f"4. **Trailing too tight?** {ma.get('4_trailing_too_tight')}",
            f"5. **Trailing too loose?** {ma.get('5_trailing_too_loose')}",
            f"6. **Board improvement:** {ma.get('6_board_improvement')} "
            f"(T3={ma.get('6_board_high_t3_delta')}, T4={ma.get('6_board_low_t4_delta')})",
            f"7. **6/18 EXIT improvable?** {ma.get('7_day618_exit_improvable')} (PnL={ma.get('7_day618_pnl')})",
            f"8. **6/22 extendable?** {ma.get('8_day622_more_extendable')} (PnL={ma.get('8_day622_pnl')})",
            f"9. **Best shadow:** {ma.get('9_best_shadow')} (delta={ma.get('9_best_shadow_delta_pnl')})",
            f"10. **Runtime candidates:** {ma.get('10_runtime_candidates')}",
            f"11. **Next phase:** {ma.get('11_next_phase')}",
            "",
            "## Outputs",
            "",
            "- `results/reports/phase560_exit_efficiency_trades.csv`",
            "- `results/reports/phase560_early_profit_take.csv`",
            "- `results/reports/phase560_opportunity_loss.csv`",
            "- `results/reports/phase560_trailing_shadow_summary.csv`",
            "- `results/reports/phase560_exit_failure_classification.csv`",
            "- `results/reports/phase560_day_compare_0618_0622.csv`",
            "- `results/reports/phase560_report.json`",
        ]
        docs.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
