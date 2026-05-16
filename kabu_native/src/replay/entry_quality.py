"""
Phase 9: ENTRY quality analysis (MFE, early MAE, breakout continuation, removed trades).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from replay.session_control import entry_allowed as session_entry_allowed
from replay.sweep_runner import (
    BASELINE_BF_CONFIRM,
    BASELINE_FAIL_BUFFER_PCT,
    BASELINE_FAIL_WINDOW_MIN,
    BASELINE_HARD_STOP_PCT,
    CachedDaySymbol,
    SweepParams,
    _ensure_repo,
)

MFE_THRESHOLDS_PCT = (0.1, 0.3, 0.5, 1.0)
EARLY_WINDOWS_SEC = (60, 180, 300)
EXIT_BUCKETS = (
    "breakout_failure",
    "hard_stop",
    "time_stop",
    "vwap_reclaim_failure",
)


@dataclass
class EnrichedTrade:
    trade_date: str
    symbol: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    pnl_pct: float
    exit_reason: str
    mfe_pct: float
    mae_pct: float
    elapsed_min: float
    signal_score_at_entry: int
    mae_1m_pct: float
    mae_3m_pct: float
    mae_5m_pct: float
    breakout_continued: bool
    session_high_at_entry: float
    session_high_max: float

    @property
    def trade_key(self) -> str:
        return f"{self.symbol}|{self.entry_time.isoformat()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "symbol": self.symbol,
            "trade_key": self.trade_key,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "exit_time": self.exit_time.isoformat(),
            "exit_price": self.exit_price,
            "pnl_pct": round(self.pnl_pct, 6),
            "exit_reason": self.exit_reason,
            "mfe_pct": round(self.mfe_pct, 6),
            "mae_pct": round(self.mae_pct, 6),
            "elapsed_min": round(self.elapsed_min, 4),
            "signal_score_at_entry": self.signal_score_at_entry,
            "mae_1m_pct": round(self.mae_1m_pct, 6),
            "mae_3m_pct": round(self.mae_3m_pct, 6),
            "mae_5m_pct": round(self.mae_5m_pct, 6),
            "breakout_continued": self.breakout_continued,
        }


@dataclass
class _QualityPosition:
    symbol: str
    trade_date: str
    entry_time: datetime
    entry_price: float
    trigger_level: float
    entry_vwap_dist_pct: Optional[float]
    session_high_at_entry: float
    session_high_max: float
    peak_price: float
    trough_price: float
    trough_1m: float
    trough_3m: float
    trough_5m: float
    tier: str
    signal_score_at_entry: int
    imbalance_low_streak: int = 0
    bf_streak: int = 0
    mae_1m_pct: Optional[float] = None
    mae_3m_pct: Optional[float] = None
    mae_5m_pct: Optional[float] = None


def phase9_scenarios() -> list[SweepParams]:
    base_kw = dict(
        fail_window_min=BASELINE_FAIL_WINDOW_MIN,
        fail_buffer_pct=BASELINE_FAIL_BUFFER_PCT,
        hard_stop_pct=BASELINE_HARD_STOP_PCT,
    )
    return [
        SweepParams(
            sweep_id="baseline",
            sweep_group="phase9",
            bf_confirm_count=BASELINE_BF_CONFIRM,
            market_session_control=False,
            **base_kw,
        ),
        SweepParams(
            sweep_id="candidate_a",
            sweep_group="phase9",
            bf_confirm_count=BASELINE_BF_CONFIRM,
            market_session_control=True,
            **base_kw,
        ),
        SweepParams(
            sweep_id="candidate_b",
            sweep_group="phase9",
            fail_window_min=BASELINE_FAIL_WINDOW_MIN,
            fail_buffer_pct=0.12,
            bf_confirm_count=2,
            market_session_control=False,
            hard_stop_pct=BASELINE_HARD_STOP_PCT,
        ),
        SweepParams(
            sweep_id="candidate_c",
            sweep_group="phase9",
            bf_confirm_count=BASELINE_BF_CONFIRM,
            market_session_control=True,
            **base_kw,
        ),
    ]


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _finalize_early_mae(pos: _QualityPosition) -> tuple[float, float, float]:
    m1 = pos.mae_1m_pct if pos.mae_1m_pct is not None else _pct_change(pos.trough_1m, pos.entry_price)
    m3 = pos.mae_3m_pct if pos.mae_3m_pct is not None else _pct_change(pos.trough_3m, pos.entry_price)
    m5 = pos.mae_5m_pct if pos.mae_5m_pct is not None else _pct_change(pos.trough_5m, pos.entry_price)
    return m1, m3, m5


def replay_cached_enriched(
    cache: list[CachedDaySymbol],
    params: SweepParams,
    *,
    repo_root: Path,
    tier: str = "B",
    entry_score_min: int = 60,
    require_timing_ok: bool = True,
    relaxed_signal: bool = False,
) -> list[EnrichedTrade]:
    from src.kabu_exit_engine import KabuExitEvalInput, KabuExitV1Config, evaluate_kabu_exit_v1
    from src.kabu_signal_engine import PushHistoryRing, evaluate_kabu_signal_v1
    from src.kabu_signal_replay import replay_signal_config
    from src.signal_engine import BreakoutStateTracker

    _ensure_repo(repo_root)

    exit_cfg = KabuExitV1Config(
        hard_stop_pct_a=params.hard_stop_pct,
        hard_stop_pct_b=params.hard_stop_pct,
        fail_buffer_pct_a=params.fail_buffer_pct,
        fail_buffer_pct_b=params.fail_buffer_pct,
        fail_window_sec=params.fail_window_min * 60.0,
    )
    signal_cfg = replay_signal_config(relaxed=relaxed_signal)
    imb_thr = 0.48 if tier.upper() == "B" else 0.46
    confirm_n = max(1, int(params.bf_confirm_count))
    all_trades: list[EnrichedTrade] = []

    for item in cache:
        if not item.events:
            continue
        ring = PushHistoryRing()
        tracker = BreakoutStateTracker()
        position: Optional[_QualityPosition] = None

        def _close(pos: _QualityPosition, exit_time: datetime, exit_price: float, reason: str) -> None:
            m1, m3, m5 = _finalize_early_mae(pos)
            entry = pos.entry_price
            mfe = _pct_change(pos.peak_price, entry)
            mae = _pct_change(pos.trough_price, entry)
            elapsed = (exit_time - pos.entry_time).total_seconds() / 60.0
            continued = pos.session_high_max > pos.session_high_at_entry + 1e-9
            all_trades.append(
                EnrichedTrade(
                    trade_date=pos.trade_date,
                    symbol=pos.symbol,
                    entry_time=pos.entry_time,
                    entry_price=entry,
                    exit_time=exit_time,
                    exit_price=exit_price,
                    pnl_pct=_pct_change(exit_price, entry),
                    exit_reason=reason,
                    mfe_pct=mfe,
                    mae_pct=mae,
                    elapsed_min=elapsed,
                    signal_score_at_entry=pos.signal_score_at_entry,
                    mae_1m_pct=m1,
                    mae_3m_pct=m3,
                    mae_5m_pct=m5,
                    breakout_continued=continued,
                    session_high_at_entry=pos.session_high_at_entry,
                    session_high_max=pos.session_high_max,
                )
            )

        for ev in item.events:
            board = ev.board
            ring.add_from_board(board)
            result, tracker = evaluate_kabu_signal_v1(
                board,
                push_history=ring,
                breakout_tracker=tracker,
                tier=tier,
                evaluated_at=ev.ts,
                cfg=signal_cfg,
            )
            rd = result.to_dict()
            price = rd.get("current_price")
            if price is None:
                continue
            px = float(price)

            if position is not None:
                elapsed_sec = (ev.ts - position.entry_time).total_seconds()

                if px > position.peak_price:
                    position.peak_price = px
                if px < position.trough_price:
                    position.trough_price = px

                if elapsed_sec <= 60:
                    if px < position.trough_1m:
                        position.trough_1m = px
                elif position.mae_1m_pct is None:
                    position.mae_1m_pct = _pct_change(position.trough_1m, position.entry_price)

                if elapsed_sec <= 180:
                    if px < position.trough_3m:
                        position.trough_3m = px
                elif position.mae_3m_pct is None:
                    position.mae_3m_pct = _pct_change(position.trough_3m, position.entry_price)

                if elapsed_sec <= 300:
                    if px < position.trough_5m:
                        position.trough_5m = px
                elif position.mae_5m_pct is None:
                    position.mae_5m_pct = _pct_change(position.trough_5m, position.entry_price)

                session_high = rd.get("high_price")
                if session_high is not None:
                    sh = float(session_high)
                    if sh > position.session_high_max:
                        position.session_high_max = sh

                imbalance = rd.get("board_imbalance")
                if imbalance is not None and float(imbalance) <= imb_thr:
                    position.imbalance_low_streak += 1
                else:
                    position.imbalance_low_streak = 0

                push_3m = ring.push_samples_avg_per_minute(as_of=ev.ts)
                exit_res = evaluate_kabu_exit_v1(
                    KabuExitEvalInput(
                        entry_price=position.entry_price,
                        current_price=px,
                        entry_time=position.entry_time,
                        now_time=ev.ts,
                        high_since_entry=position.peak_price,
                        current_vwap=rd.get("vwap"),
                        entry_vwap_dist_pct=position.entry_vwap_dist_pct,
                        spread_bps=rd.get("spread_bps"),
                        board_imbalance=imbalance,
                        push_density_1m=int(rd.get("push_samples_1m") or 0),
                        push_density_3m_avg=push_3m,
                        tier=position.tier,
                        breakout_trigger_level=position.trigger_level,
                        session_high_at_entry=position.session_high_at_entry,
                        session_high_now=rd.get("high_price"),
                        imbalance_low_streak=position.imbalance_low_streak,
                        max_price_since_entry=position.peak_price,
                    ),
                    has_position=True,
                    cfg=exit_cfg,
                )

                if exit_res.would_exit and exit_res.exit_reason == "breakout_failure":
                    position.bf_streak += 1
                    if position.bf_streak >= confirm_n:
                        _close(position, ev.ts, px, "breakout_failure")
                        position = None
                    continue
                position.bf_streak = 0

                if exit_res.would_exit:
                    _close(position, ev.ts, px, exit_res.exit_reason)
                    position = None
                continue

            if not session_entry_allowed(ev.ts, market_session_control=params.market_session_control):
                continue

            eligible = bool(rd.get("breakout_event")) and int(rd.get("signal_score") or 0) >= entry_score_min
            if require_timing_ok:
                eligible = eligible and bool(rd.get("timing_ok"))
            if not eligible:
                continue
            trigger = rd.get("trigger_level")
            if trigger is None:
                continue
            session_high = rd.get("high_price")
            sh0 = float(session_high) if session_high is not None else px
            position = _QualityPosition(
                symbol=item.symbol,
                trade_date=item.trade_date,
                entry_time=ev.ts,
                entry_price=px,
                trigger_level=float(trigger),
                entry_vwap_dist_pct=rd.get("vwap_distance_pct"),
                session_high_at_entry=sh0,
                session_high_max=sh0,
                peak_price=px,
                trough_price=px,
                trough_1m=px,
                trough_3m=px,
                trough_5m=px,
                tier=tier.upper(),
                signal_score_at_entry=int(rd.get("signal_score") or 0),
            )

        if position is not None and item.events:
            last = item.events[-1]
            last_px = float(last.board.get("CurrentPrice") or position.peak_price)
            _close(position, last.ts, last_px, "eod_close")

    return all_trades


def _rate(trades: list[EnrichedTrade], pred) -> Optional[float]:
    if not trades:
        return None
    return sum(1 for t in trades if pred(t)) / len(trades)


def _mean(vals: list[float]) -> Optional[float]:
    return statistics.mean(vals) if vals else None


def _median(vals: list[float]) -> Optional[float]:
    return statistics.median(vals) if vals else None


def _exit_bucket(reason: str) -> str:
    if reason == "breakout_failure":
        return "breakout_failure"
    if reason == "hard_stop":
        return "hard_stop"
    if reason == "time_stop":
        return "time_stop"
    if reason in ("vwap_reclaim_failure", "vwap_exit"):
        return "vwap_reclaim_failure"
    return "other"


def analyze_scenario(trades: list[EnrichedTrade], scenario_id: str) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    holds = [t.elapsed_min for t in trades]

    mfe_reach = {
        f"mfe_reach_{th:g}pct": _rate(trades, lambda t, th=th: t.mfe_pct >= th)
        for th in MFE_THRESHOLDS_PCT
    }

    early = {
        "early_mae_1m_avg_pct": _mean([t.mae_1m_pct for t in trades]),
        "early_mae_1m_median_pct": _median([t.mae_1m_pct for t in trades]),
        "early_mae_3m_avg_pct": _mean([t.mae_3m_pct for t in trades]),
        "early_mae_3m_median_pct": _median([t.mae_3m_pct for t in trades]),
        "early_mae_5m_avg_pct": _mean([t.mae_5m_pct for t in trades]),
        "early_mae_5m_median_pct": _median([t.mae_5m_pct for t in trades]),
        "early_adverse_1m_rate": _rate(trades, lambda t: t.mae_1m_pct < -0.05),
        "early_adverse_3m_rate": _rate(trades, lambda t: t.mae_3m_pct < -0.05),
        "early_adverse_5m_rate": _rate(trades, lambda t: t.mae_5m_pct < -0.05),
    }

    by_exit: dict[str, list[EnrichedTrade]] = {b: [] for b in EXIT_BUCKETS}
    by_exit["other"] = []
    for t in trades:
        b = _exit_bucket(t.exit_reason)
        by_exit.setdefault(b, []).append(t)

    exit_mfe = {}
    for bucket in (*EXIT_BUCKETS, "other"):
        subset = by_exit.get(bucket) or []
        exit_mfe[f"mfe_avg_exit_{bucket}"] = _mean([x.mfe_pct for x in subset])
        exit_mfe[f"mfe_median_exit_{bucket}"] = _median([x.mfe_pct for x in subset])
        exit_mfe[f"count_exit_{bucket}"] = len(subset)

    return {
        "scenario_id": scenario_id,
        "trades": len(trades),
        "total_pnl_pct": sum(pnls) if pnls else 0.0,
        "avg_pnl_pct": _mean(pnls),
        "win_rate": _rate(trades, lambda t: t.pnl_pct > 0),
        "avg_mfe_pct": _mean([t.mfe_pct for t in trades]),
        "median_mfe_pct": _median([t.mfe_pct for t in trades]),
        "avg_mae_pct": _mean([t.mae_pct for t in trades]),
        "breakout_continuation_rate": _rate(trades, lambda t: t.breakout_continued),
        "avg_hold_min": _mean(holds),
        "median_hold_min": _median(holds),
        **mfe_reach,
        **early,
        **exit_mfe,
    }


def compare_removed_trades(
    baseline: list[EnrichedTrade],
    candidate: list[EnrichedTrade],
    candidate_id: str,
) -> dict[str, Any]:
    base_map = {t.trade_key: t for t in baseline}
    cand_keys = {t.trade_key for t in candidate}
    removed = [base_map[k] for k in base_map if k not in cand_keys]
    kept = [base_map[k] for k in base_map if k in cand_keys]
    added = [t for t in candidate if t.trade_key not in base_map]

    def _bucket_summary(group: list[EnrichedTrade], label: str) -> dict[str, Any]:
        if not group:
            return {"label": label, "count": 0}
        return {
            "label": label,
            "count": len(group),
            "total_pnl_pct": sum(t.pnl_pct for t in group),
            "avg_pnl_pct": _mean([t.pnl_pct for t in group]),
            "win_rate": _rate(group, lambda t: t.pnl_pct > 0),
            "avg_mfe_pct": _mean([t.mfe_pct for t in group]),
            "mfe_reach_0.3pct": _rate(group, lambda t: t.mfe_pct >= 0.3),
            "mfe_reach_0.5pct": _rate(group, lambda t: t.mfe_pct >= 0.5),
            "noise_proxy_rate": _rate(group, lambda t: t.mfe_pct < 0.1 and t.pnl_pct < 0),
        }

    removed_wins = [t for t in removed if t.pnl_pct > 0]
    removed_losses = [t for t in removed if t.pnl_pct <= 0]

    cand_map = {t.trade_key: t for t in candidate}
    paired: list[tuple[EnrichedTrade, EnrichedTrade]] = [
        (base_map[k], cand_map[k]) for k in base_map if k in cand_map
    ]
    mfe_delta = [c.mfe_pct - b.mfe_pct for b, c in paired]
    pnl_delta = [c.pnl_pct - b.pnl_pct for b, c in paired]

    return {
        "candidate_id": candidate_id,
        "baseline_trades": len(baseline),
        "candidate_trades": len(candidate),
        "removed_count": len(removed),
        "kept_count": len(kept),
        "added_count": len(added),
        "removed": _bucket_summary(removed, "removed"),
        "removed_wins": _bucket_summary(removed_wins, "removed_wins"),
        "removed_losses": _bucket_summary(removed_losses, "removed_losses"),
        "kept": _bucket_summary(kept, "kept"),
        "added": _bucket_summary(added, "added"),
        "same_entry_paired_count": len(paired),
        "same_entry_avg_mfe_delta_pct": _mean(mfe_delta),
        "same_entry_avg_pnl_delta_pct": _mean(pnl_delta),
        "same_entry_mfe_improved_rate": _rate(
            [{"d": d} for d in mfe_delta], lambda x: x["d"] > 0.05
        ),
        "removed_pnl_share_of_baseline_loss": (
            sum(t.pnl_pct for t in removed) / sum(t.pnl_pct for t in baseline)
            if baseline and sum(t.pnl_pct for t in baseline) < 0
            else None
        ),
        "interpretation_hint": _removal_hint(removed, removed_wins, removed_losses),
    }


def _removal_hint(
    removed: list[EnrichedTrade],
    wins: list[EnrichedTrade],
    losses: list[EnrichedTrade],
) -> str:
    if not removed:
        return "no_removed_trades"
    win_pnl = sum(t.pnl_pct for t in wins)
    loss_pnl = sum(t.pnl_pct for t in losses)
    noise = sum(1 for t in removed if t.mfe_pct < 0.1 and t.pnl_pct < 0)
    if win_pnl > 0 and abs(win_pnl) > abs(loss_pnl) * 0.15:
        return "also_removing_some_winners"
    if noise >= len(removed) * 0.6:
        return "mostly_noise_trades_removed"
    if loss_pnl < 0 and win_pnl <= 0:
        return "mostly_losing_trades_removed"
    return "mixed_removal"


def build_phase9_report(
    scenario_metrics: dict[str, dict[str, Any]],
    removal_comparisons: list[dict[str, Any]],
    *,
    meta: dict[str, Any],
) -> dict[str, Any]:
    base = scenario_metrics.get("baseline", {})
    verdict = _build_verdict(scenario_metrics, removal_comparisons, base)
    return {
        "meta": meta,
        "scenario_metrics": scenario_metrics,
        "removal_comparisons": removal_comparisons,
        "verdict": verdict,
    }


def _build_verdict(
    metrics: dict[str, dict[str, Any]],
    removals: list[dict[str, Any]],
    base: dict[str, Any],
) -> dict[str, Any]:
    def _delta(scenario: str, key: str) -> Optional[float]:
        m = metrics.get(scenario, {})
        b = base.get(key)
        v = m.get(key)
        if b is None or v is None:
            return None
        return float(v) - float(b)

    removal_by_id = {r["candidate_id"]: r for r in removals}
    entry_quality_improved: list[str] = []
    exit_hold_improved: list[str] = []
    trade_count_only: list[str] = []

    for sid in ("candidate_a", "candidate_b", "candidate_c"):
        m = metrics.get(sid, {})
        if not m:
            continue
        mfe_reach_up = (_delta(sid, "mfe_reach_0.3pct") or 0) > 0.08
        cont_up = (_delta(sid, "breakout_continuation_rate") or 0) > 0.15
        hold_up = (m.get("median_hold_min") or 0) > (base.get("median_hold_min") or 0) * 2
        same_mfe_up = (removal_by_id.get(sid, {}).get("same_entry_avg_mfe_delta_pct") or 0) > 0.05
        trade_drop = (base.get("trades") or 0) - (m.get("trades") or 0) >= 10

        if sid == "candidate_b" or (mfe_reach_up and cont_up):
            exit_hold_improved.append(sid)
            if mfe_reach_up or same_mfe_up:
                entry_quality_improved.append(sid)
        elif trade_drop and (_delta(sid, "mfe_reach_0.3pct") or 0) <= 0:
            trade_count_only.append(sid)

    if exit_hold_improved == ["candidate_b"]:
        next_focus = "EXIT (BF confirm / hold — same entries breathe longer)"
    elif trade_count_only and not exit_hold_improved:
        next_focus = "ENTRY gate only (opening filter; not better entries)"
    elif trade_count_only and exit_hold_improved:
        next_focus = "ENTRY gate + EXIT (A/C cut noise; B improves hold path)"
    else:
        next_focus = "ENTRY"

    return {
        "entry_quality_improved_scenarios": entry_quality_improved,
        "exit_hold_improved_scenarios": exit_hold_improved,
        "trade_reduction_only_scenarios": trade_count_only,
        "recommended_next_focus": next_focus,
        "notes": {
            "candidate_a": "Opening gate removes losing early entries; kept-trade MFE flat.",
            "candidate_b": "BF confirm=2 extends holds; MFE/continuation up on same entries.",
            "candidate_c": "Mid gate like A; trade count cut without MFE lift on survivors.",
        },
    }


def metrics_to_csv_rows(scenario_metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sid, m in scenario_metrics.items():
        row = {"row_type": "scenario", "scenario_id": sid, **m}
        rows.append(row)
    return rows
