"""
Phase389: Full-regime live candidate validation (Stack C).

Re-evaluates 1.5M / credit 2x / CAP=2 across Period A+B (20260518–20260612).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase377_daily_regime_breakdown import (
    PERIOD_A_END,
    PERIOD_A_ID,
    PERIOD_A_START,
    PERIOD_B_END,
    PERIOD_B_ID,
    PERIOD_B_START,
    PRIMARY_STACK,
)
from research.phase379_380_period_b_eval import is_low_mfe_stop, is_stop_hit
from research.phase382_capital_constrained_backtest import (
    HARD_STOP_PCT,
    MAINT_FORCE_EXIT,
    MAINT_STOP_ENTRY,
    MAINT_WARNING,
    _day_from_ts,
    _float,
    _parse_ts,
    _pf,
    _position_key,
    _write_csv,
    dedupe_trades,
)
from research.phase388_cap1500k_live_candidate_validation import (
    CANDIDATE_CAP,
    CANDIDATE_EQUITY,
    TRADE_LOG_FIELDS,
    build_daily_equity_rows,
    simulate_detailed,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_DAY = PERIOD_A_START
DEFAULT_MAX_DAY = PERIOD_B_END
PHASE377_REFERENCE_JSON = "phase377_daily_regime_breakdown_summary.json"
PHASE388_REFERENCE_JSON = "phase388_cap1500k_validation_summary.json"

DAILY_EQUITY_FIELDS = [
    "day",
    "period_id",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
    "min_maintenance_ratio",
    "max_gross_position_value",
    "accepted_trade_count",
    "rejected_trade_count",
]

PERIOD_A_LOSS_FIELDS = [
    "rank",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen",
    "peak_mfe_pct",
    "entry_score",
    "entry_momentum_score",
    "price_range_position",
    "board_tier",
    "exit_reason",
    "universe_group",
    "dynamic40_rank_bucket",
]


def period_id_for_day(day: str) -> str:
    if PERIOD_A_START <= day <= PERIOD_A_END:
        return PERIOD_A_ID
    if PERIOD_B_START <= day <= PERIOD_B_END:
        return PERIOD_B_ID
    return "unknown"


def _board_tier(trade: Mapping[str, Any]) -> str:
    return str(
        trade.get("board_dynamic_tier")
        or trade.get("board_dynamic_trailing_tier")
        or trade.get("board_tier")
        or "unknown"
    )


def _entry_score(trade: Mapping[str, Any]) -> Optional[float]:
    for key in ("entry_score_v2", "entry_expectancy_score_v2", "entry_score", "entry_momentum_score"):
        val = _float(trade.get(key))
        if val is not None:
            return val
    return None


def accepted_trades_from_sim(
    sim: Mapping[str, Any],
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    lookup = {_position_key(t): dict(t) for t in trades}
    rows: list[dict[str, Any]] = []
    for log in sim.get("_trade_log") or []:
        if log.get("accepted_or_rejected") != "accepted" or log.get("pnl_yen") in ("", None):
            continue
        key = _position_key({"symbol": log.get("symbol"), "entry_time": log.get("entry_time")})
        trade = lookup.get(key, {})
        entry_day = _day_from_ts(str(log.get("entry_time") or trade.get("entry_time") or "")) or ""
        exit_day = _day_from_ts(str(log.get("exit_time") or trade.get("exit_time") or "")) or ""
        rows.append(
            {
                "symbol": log.get("symbol"),
                "entry_time": log.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "entry_day": entry_day,
                "exit_day": exit_day,
                "day": exit_day or entry_day,
                "period_id": period_id_for_day(entry_day),
                "pnl_yen": float(log.get("pnl_yen") or 0.0),
                "pnl_yen_100": float(log.get("pnl_yen") or 0.0),
                "exit_reason": str(trade.get("exit_reason_canonical") or trade.get("exit_reason") or log.get("exit_reason") or ""),
                "exit_reason_canonical": str(trade.get("exit_reason_canonical") or trade.get("exit_reason") or log.get("exit_reason") or ""),
                "universe_group": str(trade.get("universe_group") or ""),
                "peak_mfe_pct": _float(trade.get("peak_mfe_pct")),
                "entry_score": _entry_score(trade),
                "entry_momentum_score": _float(trade.get("entry_momentum_score")),
                "price_range_position": _float(trade.get("price_range_position")),
                "board_tier": _board_tier(trade),
                "dynamic40_rank_bucket": trade.get("dynamic40_rank_bucket") or "",
                "trade": trade,
            }
        )
    return rows


def regime_metrics(
    accepted: Sequence[Mapping[str, Any]],
    *,
    period_id: str,
    daily_pnls: Mapping[str, float],
    equity_curve: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    subset = [t for t in accepted if t.get("period_id") == period_id]
    pnls = [float(t.get("pnl_yen") or 0.0) for t in subset]
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in subset if is_stop_hit(t))
    low_mfe = sum(1 for t in subset if is_low_mfe_stop(t))
    trailing = sum(1 for t in subset if str(t.get("exit_reason") or "") == "trailing_mfe_exit")
    overlap = sum(1 for t in subset if str(t.get("exit_reason") or "") == "overlap_replaced")
    d40 = sum(float(t.get("pnl_yen") or 0.0) for t in subset if str(t.get("universe_group") or "") == "dynamic40")
    c10 = sum(float(t.get("pnl_yen") or 0.0) for t in subset if str(t.get("universe_group") or "") == "core10")

    period_days = sorted(d for d in daily_pnls if period_id_for_day(d) == period_id)
    period_daily = {d: float(daily_pnls.get(d, 0.0)) for d in period_days}
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for d in period_days:
        cum += period_daily[d]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    period_equities = [
        float(p.get("equity") or 0.0)
        for p in equity_curve
        if period_id_for_day(str(p.get("day") or "")[:8]) == period_id
    ]
    eq_peak = period_equities[0] if period_equities else CANDIDATE_EQUITY
    eq_dd = 0.0
    if period_equities:
        eq_peak = period_equities[0]
        for eq in period_equities:
            eq_peak = max(eq_peak, eq)
            eq_dd = max(eq_dd, eq_peak - eq)

    return {
        "period_id": period_id,
        "period_start": PERIOD_A_START if period_id == PERIOD_A_ID else PERIOD_B_START,
        "period_end": PERIOD_A_END if period_id == PERIOD_A_ID else PERIOD_B_END,
        "trade_count": len(subset),
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
        "stop_hit_count": stops,
        "low_mfe_stop_count": low_mfe,
        "trailing_mfe_exit_count": trailing,
        "overlap_replaced_count": overlap,
        "dynamic40_pnl_yen": round(d40, 2),
        "core10_pnl_yen": round(c10, 2),
        "max_drawdown_yen": round(max(eq_dd, max_dd), 2),
        "day_count": len(period_days),
    }


def build_daily_rows_with_period(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = build_daily_equity_rows(sim)
    for row in rows:
        day = str(row.get("day") or "")
        row["period_id"] = period_id_for_day(day)
    return rows


def load_phase377_reference(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / PHASE377_REFERENCE_JSON
    if not path.is_file():
        return {"loaded": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    period_metrics = data.get("period_metrics") or {}
    by_period = {
        pid: stacks.get(PRIMARY_STACK) or {}
        for pid, stacks in period_metrics.items()
        if isinstance(stacks, Mapping) and stacks.get(PRIMARY_STACK)
    }
    return {"loaded": True, "stack_c_by_period": by_period}


def load_session_full_regime_capital_trades(
    session_meta: Mapping[str, Any],
    *,
    reports_dir: Path,
    min_day: str,
    max_day: Optional[str],
) -> dict[str, Any]:
    from research.phase381_winner_profile_review import enrich_trade_with_rank
    from research.phase382_capital_constrained_backtest import load_session_capital_backtest_trades
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    result = load_session_capital_backtest_trades(
        session_meta,
        reports_dir=reports_dir,
        min_day=min_day,
        max_day=max_day,
    )
    if result.get("error") and not result.get("valid_trades"):
        return {**result, "all_trades": []}

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    try:
        sess_dir = Path(str(session_meta["session_dir"]))
        for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
            if row.get("event_type") == "accepted":
                accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row
    except Exception:
        pass

    enriched: list[dict[str, Any]] = []
    for trade in result.get("valid_trades") or []:
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        row = enrich_trade_with_rank(trade, acc, session_meta=session_meta, reports_dir=reports_dir)
        enriched.append(row)

    return {
        **result,
        "all_trades": enriched,
        "trade_count": len(enriched),
        "error": "",
    }


def load_phase388_reference(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / PHASE388_REFERENCE_JSON
    if not path.is_file():
        return {"loaded": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    cand = data.get("candidate") or {}
    return {
        "loaded": True,
        "period": data.get("population"),
        "total_pnl_yen": cand.get("total_pnl_yen"),
        "profit_factor": cand.get("profit_factor"),
        "accepted_trade_count": cand.get("accepted_trade_count"),
        "min_maintenance_ratio": cand.get("min_maintenance_ratio"),
    }


def period_a_reject_stats(trade_log: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    period_a_rows = [
        r
        for r in trade_log
        if period_id_for_day(_day_from_ts(str(r.get("entry_time") or "")) or "") == PERIOD_A_ID
    ]
    rejected = [r for r in period_a_rows if r.get("accepted_or_rejected") == "rejected"]
    accepted = [r for r in period_a_rows if r.get("accepted_or_rejected") == "accepted"]
    return {
        "period_a_signal_count": len(period_a_rows),
        "period_a_entries_accepted": len(accepted),
        "period_a_entries_rejected": len(rejected),
        "period_a_reject_reason_breakdown": dict(Counter(str(r.get("reject_reason") or "") for r in rejected)),
    }


def build_period_a_loss_analysis(
    accepted: Sequence[Mapping[str, Any]],
    *,
    reject_breakdown: Mapping[str, int],
    trade_log: Optional[Sequence[Mapping[str, Any]]] = None,
    phase377_period_a: Optional[Mapping[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    period_a = [t for t in accepted if t.get("period_id") == PERIOD_A_ID]
    losses = sorted([t for t in period_a if float(t.get("pnl_yen") or 0.0) < 0], key=lambda t: float(t.get("pnl_yen") or 0.0))
    top50 = losses[:50]

    rows: list[dict[str, Any]] = []
    for i, t in enumerate(top50, start=1):
        rows.append(
            {
                "rank": i,
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "pnl_yen": round(float(t.get("pnl_yen") or 0.0), 2),
                "peak_mfe_pct": t.get("peak_mfe_pct"),
                "entry_score": t.get("entry_score"),
                "entry_momentum_score": t.get("entry_momentum_score"),
                "price_range_position": t.get("price_range_position"),
                "board_tier": t.get("board_tier"),
                "exit_reason": t.get("exit_reason"),
                "universe_group": t.get("universe_group"),
                "dynamic40_rank_bucket": t.get("dynamic40_rank_bucket"),
            }
        )

    exit_ctr = Counter(str(t.get("exit_reason") or "") for t in losses)
    low_mfe_losses = sum(1 for t in losses if is_low_mfe_stop(t))
    stop_losses = sum(1 for t in losses if is_stop_hit(t))
    scores = [float(t.get("entry_score")) for t in losses if t.get("entry_score") is not None]
    mfes = [float(t.get("peak_mfe_pct")) for t in losses if t.get("peak_mfe_pct") is not None]

    total_loss = round(sum(float(t.get("pnl_yen") or 0.0) for t in losses), 2)
    capital_rejects = int(reject_breakdown.get("insufficient_buying_power") or 0) + int(
        reject_breakdown.get("max_concurrent_positions") or 0
    )
    gating = period_a_reject_stats(trade_log or []) if trade_log else {}
    unconstrained_a = float((phase377_period_a or {}).get("total_pnl_yen_100") or 0.0)
    period_a_total_pnl = round(sum(float(t.get("pnl_yen") or 0.0) for t in period_a), 2)

    if low_mfe_losses >= stop_losses * 0.5 and stop_losses > 0:
        primary_cause = "entry_quality"
    elif exit_ctr.get("trailing_mfe_exit", 0) > exit_ctr.get("stop_hit", 0):
        primary_cause = "exit_timing"
    elif gating.get("period_a_entries_rejected", 0) > max(len(period_a), 1) * 10:
        primary_cause = "capital_management"
    elif capital_rejects > len(period_a) * 2:
        primary_cause = "capital_management"
    else:
        primary_cause = "mixed_entry_exit"

    summary = {
        "period_a_entry_trade_count": len(period_a),
        "period_a_total_pnl_yen": period_a_total_pnl,
        "period_a_loss_trade_count": len(losses),
        "period_a_total_loss_yen": total_loss,
        "top50_loss_yen": round(sum(float(r.get("pnl_yen") or 0.0) for r in rows), 2),
        "exit_reason_counts": dict(exit_ctr),
        "stop_hit_loss_count": stop_losses,
        "low_mfe_stop_loss_count": low_mfe_losses,
        "avg_entry_score_losses": round(statistics.mean(scores), 4) if scores else None,
        "avg_peak_mfe_pct_losses": round(statistics.mean(mfes), 4) if mfes else None,
        "board_tier_counts": dict(Counter(str(t.get("board_tier") or "") for t in losses)),
        "period_a_cap_gating": gating,
        "phase377_unconstrained_period_a_pnl_yen": unconstrained_a,
        "diagnosis": {
            "primary_cause": primary_cause,
            "capital_management_contribution": (
                "primary"
                if gating.get("period_a_entries_rejected", 0) > len(period_a) * 10
                else "secondary"
            ),
            "entry_quality_signal": low_mfe_losses > 0 or (statistics.mean(scores) if scores else 99) < 3.0,
            "exit_problem_signal": exit_ctr.get("trailing_mfe_exit", 0) + exit_ctr.get("overlap_replaced", 0) > stop_losses,
            "note": (
                "Period A: CAP=2 saturated from first session; most bad-regime signals blocked. "
                f"Unconstrained Stack C would have lost {unconstrained_a}円 in Period A."
            ),
        },
    }
    return rows, summary


def build_required_answers(
    full: Mapping[str, Any],
    regime_a: Mapping[str, Any],
    regime_b: Mapping[str, Any],
) -> dict[str, Any]:
    total_pnl = float(full.get("total_pnl_yen") or 0.0)
    a_pnl = float(regime_a.get("total_pnl_yen") or 0.0)
    b_pnl = float(regime_b.get("total_pnl_yen") or 0.0)
    min_mr = full.get("min_maintenance_ratio")
    min_mr_f = float(min_mr) if min_mr is not None else 1.0
    force_exit = int(full.get("force_exit_count") or 0)

    maintenance_safe = min_mr_f >= MAINT_STOP_ENTRY and force_exit == 0
    margin_call_risk = force_exit > 0 or min_mr_f < MAINT_WARNING or int(full.get("maintenance_stop_count") or 0) > 0
    live_viable = total_pnl > 0 and force_exit == 0 and not full.get("equity_floor_breached")
    recommend_1500k = live_viable and maintenance_safe

    return {
        "full_period_profitable": total_pnl > 0,
        "period_a_pnl_yen": a_pnl,
        "period_b_pnl_yen": b_pnl,
        "period_b_recovery_yen": round(b_pnl + abs(min(a_pnl, 0.0)), 2) if a_pnl < 0 else b_pnl,
        "final_equity_yen": full.get("final_equity"),
        "max_drawdown_yen": full.get("max_drawdown_yen"),
        "maintenance_safe": maintenance_safe,
        "min_maintenance_ratio": min_mr,
        "margin_call_risk": margin_call_risk,
        "live_operation_viable": live_viable,
        "recommend_1500k": recommend_1500k,
        "recommendation": "150万円運用を推奨" if recommend_1500k else "推奨しない",
        "recommendation_reason": (
            f"全期間黒字({total_pnl}円)、Period B回復({b_pnl}円)、維持率安全(min_maint={min_mr})"
            if recommend_1500k
            else (
                f"全期間{'赤字' if total_pnl <= 0 else '黒字だがリスク'}; Period A={a_pnl}円"
                + (f"; 必要元本は200万円以上を検討" if not maintenance_safe or total_pnl <= 0 else "")
            )
        ),
        "required_capital_if_not_recommended_yen": None if recommend_1500k else 2_000_000,
    }


def build_report(summary: Mapping[str, Any]) -> str:
    ans = summary.get("required_answers") or {}
    full = summary.get("full_period") or {}
    ra = summary.get("regime_period_a") or {}
    rb = summary.get("regime_period_b") or {}
    loss = summary.get("period_a_loss_diagnosis") or {}
    p377 = summary.get("phase377_reference") or {}
    p388 = summary.get("phase388_reference") or {}
    c_a = (p377.get("stack_c_by_period") or {}).get(PERIOD_A_ID) or {}
    c_b = (p377.get("stack_c_by_period") or {}).get(PERIOD_B_ID) or {}
    lines = [
        "# Phase389 Full-Regime Live Candidate Validation",
        "",
        f"**期間:** {summary.get('population', {}).get('min_day')}–{summary.get('population', {}).get('max_day')}",
        "**候補:** 150万円 / 信用2倍 / 100株 / CAP=2 / Stack C",
        "",
        "## 必須回答",
        "",
        f"- **全期間でも黒字か:** {'はい' if ans.get('full_period_profitable') else 'いいえ'} ({full.get('total_pnl_yen')}円)",
        f"- **Period A損失:** {ans.get('period_a_pnl_yen')}円",
        f"- **Period B回復:** {ans.get('period_b_pnl_yen')}円",
        f"- **最終資産:** {ans.get('final_equity_yen')}円",
        f"- **最大DD:** {ans.get('max_drawdown_yen')}円",
        f"- **維持率安全:** {'はい' if ans.get('maintenance_safe') else 'いいえ'} (min_maint={ans.get('min_maintenance_ratio')})",
        f"- **追証リスク:** {'あり' if ans.get('margin_call_risk') else 'なし'}",
        f"- **ライブ運用可能:** {'はい' if ans.get('live_operation_viable') else 'いいえ'}",
        f"- **150万円推奨:** {ans.get('recommendation')}",
        f"- **理由:** {ans.get('recommendation_reason')}",
        "",
        "## レジーム別",
        "",
        "| 期間 | trades | PnL | PF | win_rate | stop | low_mfe | max_dd | D40 | C10 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| Period A | {ra.get('trade_count')} | {ra.get('total_pnl_yen')} | {ra.get('profit_factor')} | {ra.get('win_rate')} | {ra.get('stop_hit_count')} | {ra.get('low_mfe_stop_count')} | {ra.get('max_drawdown_yen')} | {ra.get('dynamic40_pnl_yen')} | {ra.get('core10_pnl_yen')} |",
        f"| Period B | {rb.get('trade_count')} | {rb.get('total_pnl_yen')} | {rb.get('profit_factor')} | {rb.get('win_rate')} | {rb.get('stop_hit_count')} | {rb.get('low_mfe_stop_count')} | {rb.get('max_drawdown_yen')} | {rb.get('dynamic40_pnl_yen')} | {rb.get('core10_pnl_yen')} |",
        "",
        "## Period A損失診断",
        "",
        f"- primary_cause: {loss.get('diagnosis', {}).get('primary_cause')}",
        f"- Period A entries (closed): {loss.get('period_a_entry_trade_count')} pnl={loss.get('period_a_total_pnl_yen')}円",
        f"- Period A cap gating: accepted={loss.get('period_a_cap_gating', {}).get('period_a_entries_accepted')} rejected={loss.get('period_a_cap_gating', {}).get('period_a_entries_rejected')}",
        f"- Phase377 unconstrained Period A (参考): {loss.get('phase377_unconstrained_period_a_pnl_yen')}円",
        f"- stop_hit_losses: {loss.get('stop_hit_loss_count')} low_mfe: {loss.get('low_mfe_stop_loss_count')}",
        f"- top50_loss_total: {loss.get('top50_loss_yen')}円",
        "",
        "## 参照",
        "",
        f"- Phase377 unconstrained Period A: {c_a.get('total_pnl_yen_100')}円 (PF {c_a.get('profit_factor')})",
        f"- Phase377 unconstrained Period B: {c_b.get('total_pnl_yen_100')}円 (PF {c_b.get('profit_factor')})",
        f"- Phase388 CAP2 Period B only: {p388.get('total_pnl_yen')}円 (accepted {p388.get('accepted_trade_count')})",
        "",
        "## 禁止事項",
        "",
        "- ENTRY/EXIT/Universe/CAP変更なし",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class Phase389FullRegimeLiveCandidateValidation:
    reports_dir: Path
    min_day: str = DEFAULT_MIN_DAY
    max_day: Optional[str] = DEFAULT_MAX_DAY
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase389_full_regime_live_candidate_summary.json",
            "daily_equity": self.reports_dir / "phase389_full_regime_daily_equity.csv",
            "trade_log": self.reports_dir / "phase389_full_regime_trade_log.csv",
            "report": self.reports_dir / "phase389_full_regime_report.md",
            "period_a_loss_top50": self.reports_dir / "phase389_periodA_loss_top50.csv",
            "period_a_loss_summary": self.reports_dir / "phase389_periodA_loss_summary.json",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or result.get("valid_trades") or [])

    def run(
        self,
        *,
        wall_runtime_sec: float = 0.0,
        sessions_discovered: int = 0,
        sessions_evaluated: int = 0,
    ) -> dict[str, Any]:
        trades, duplicate_removed = dedupe_trades(self.all_trades)
        trades = sorted(
            trades,
            key=lambda t: (_parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST), str(t.get("symbol") or "")),
        )

        sim = simulate_detailed(
            trades,
            scenario_id="candidate_1500k_cap2_full_regime",
            cap=CANDIDATE_CAP,
            initial_equity=CANDIDATE_EQUITY,
        )
        accepted = accepted_trades_from_sim(sim, trades)
        daily_pnls = sim.get("_daily_pnls") or {}
        equity_curve = sim.get("_equity_curve") or []

        regime_a = regime_metrics(accepted, period_id=PERIOD_A_ID, daily_pnls=daily_pnls, equity_curve=equity_curve)
        regime_b = regime_metrics(accepted, period_id=PERIOD_B_ID, daily_pnls=daily_pnls, equity_curve=equity_curve)

        loss_rows, loss_summary = build_period_a_loss_analysis(
            accepted,
            reject_breakdown=sim.get("reject_reason_breakdown") or {},
            trade_log=sim.get("_trade_log") or [],
            phase377_period_a=(load_phase377_reference(self.reports_dir).get("stack_c_by_period") or {}).get(PERIOD_A_ID),
        )

        public_full = {k: v for k, v in sim.items() if not str(k).startswith("_")}
        required = build_required_answers(public_full, regime_a, regime_b)

        return {
            "phase": 389,
            "title": "Full-regime live candidate validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "hard_stop_pct": HARD_STOP_PCT,
            "candidate_config": {
                "initial_equity": CANDIDATE_EQUITY,
                "leverage_limit": 2.0,
                "shares": 100,
                "position_cap": CANDIDATE_CAP,
                "reinvestment": True,
            },
            "population": {
                "min_day": self.min_day,
                "max_day": self.max_day,
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
                "input_trade_count_raw": len(self.all_trades),
                "duplicate_session_trades_removed": duplicate_removed,
                "input_trade_count": len(trades),
            },
            "full_period": public_full,
            "regime_period_a": regime_a,
            "regime_period_b": regime_b,
            "phase377_reference": load_phase377_reference(self.reports_dir),
            "phase388_reference": load_phase388_reference(self.reports_dir),
            "required_answers": required,
            "period_a_loss_diagnosis": loss_summary,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "_sim": sim,
            "_daily_rows": build_daily_rows_with_period(sim),
            "_trade_log": sim.get("_trade_log") or [],
            "_period_a_loss_rows": loss_rows,
            "_trades": trades,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["daily_equity"], list(result.get("_daily_rows") or []), DAILY_EQUITY_FIELDS)
        _write_csv(paths["trade_log"], list(result.get("_trade_log") or []), TRADE_LOG_FIELDS)
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        paths["period_a_loss_top50"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["period_a_loss_top50"], list(result.get("_period_a_loss_rows") or []), PERIOD_A_LOSS_FIELDS)
        paths["period_a_loss_summary"].write_text(
            json.dumps(result.get("period_a_loss_diagnosis") or {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        trades_cache = paths["summary"].with_name("phase389_full_regime_trades_cache.json")
        trades_cache.write_text(json.dumps(list(result.get("_trades") or []), ensure_ascii=False), encoding="utf-8")
        return paths


__all__ = [
    "Phase389FullRegimeLiveCandidateValidation",
    "load_session_full_regime_capital_trades",
    "period_id_for_day",
    "regime_metrics",
]
