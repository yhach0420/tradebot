"""Phase668 — Existing shadow adoption review before new shadow (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    load_all_full_period_trades,
)
from research.phase649_flat_band_guard_counterfactual import (
    block_flat_plus_overheat,
    block_phase635_rise5_shadow,
)
from research.phase652_shadow_registry import ShadowDef, _registry_definitions
from research.phase657_shadow_portfolio_review import _discover_summaries_extended, _research_shadow_defs
from research.phase658_full_period_shadow_revalidation import (
    EvalContext,
    RISE5_THRESHOLD,
    ShadowEval,
    _bool_val,
    _enrich_trades_from_events,
    _evaluate_all_shadows,
    _load_phase657_decisions,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_misread_guard

PHASE668_VERDICT = "phase668_existing_shadow_adoption_review_done"
REPORT_DIR_NAME = "phase668_existing_shadow_adoption"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
MAX_WORKERS = 4
DISK_USAGE_MAX_PCT = 75.0
BIG_WINNER_YEN = 5000.0

PRIORITY_SHADOW_IDS: tuple[str, ...] = (
    "pbv2_flat_band_shadow",
    "pbv2_rise5_shadow",
    "pullback_misread_guard_shadow",
    "exit_shadow_monitor_t2",
    "exit_shadow_monitor_t3",
)

OTHER_RUNTIME_SHADOW_IDS: tuple[str, ...] = (
    "board_dynamic_trailing_shadow",
    "realtime_board_exit_shadow",
    "vwap_shadow_reject",
    "board_imbalance_shadow",
    "limit_up_proximity_entry_guard_shadow",
    "volume_gate_relaxation_shadow",
    "extended_entry_shadow",
    "quality_formula_shadow",
    "trading_value_shadow_gate",
    "low_liquidity_shadow",
)

RUNTIME_REVIEW_IDS: frozenset[str] = frozenset(PRIORITY_SHADOW_IDS + OTHER_RUNTIME_SHADOW_IDS)

ENTRY_BLOCK_CONFIG: dict[str, tuple[str, str, Callable[[Mapping[str, Any]], bool]]] = {
    "pbv2_rise5_shadow": (
        "PBV2_ONLY",
        "pbv2_rise5_shadow_block",
        lambda t: block_phase635_rise5_shadow(t, RISE5_THRESHOLD),
    ),
    "pbv2_flat_band_shadow": (
        "PBV2_ONLY",
        "pbv2_flat_band_shadow_block",
        block_flat_plus_overheat,
    ),
    "pullback_misread_guard_shadow": (
        "ALL",
        "pullback_misread_guard_shadow_blocked",
        would_block_pullback_misread_guard,
    ),
    "vwap_shadow_reject": (
        "ALL",
        "vwap_shadow_reject_candidate",
        lambda t: _bool_val(t.get("vwap_shadow_reject_candidate")),
    ),
    "board_imbalance_shadow": (
        "ALL",
        "imbalance_shadow_candidate",
        lambda t: _bool_val(t.get("imbalance_shadow_candidate")),
    ),
    "limit_up_proximity_entry_guard_shadow": (
        "ALL",
        "limit_up_proximity_guard_shadow_blocked",
        lambda t: _bool_val(t.get("limit_up_proximity_guard_shadow_blocked")),
    ),
}


def _is_stop_hit(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason") or "") == "stop_hit"


def _is_no_progress(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason") or "") == "no_progress_exit"


def _rate(trades: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]) -> Optional[float]:
    if not trades:
        return None
    return round(sum(1 for t in trades if pred(t)) / len(trades), 4)


def _filter_canonical(
    trades: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    days = set(CANONICAL_DAYS)
    ft = [t for t in trades if str(t.get("day") or "") in days]
    fs = [s for s in sessions if str(s.get("day") or "") in days]
    return ft, fs


def _blocked_kept_universe(
    trades: Sequence[Mapping[str, Any]],
    shadow_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if shadow_id not in ENTRY_BLOCK_CONFIG:
        universe = list(trades)
        return universe, [], universe
    pool, event_field, block_fn = ENTRY_BLOCK_CONFIG[shadow_id]
    universe = list(trades)
    if pool == "PBV2_ONLY":
        universe = [t for t in trades if str(t.get("entry_pool") or "") == "PBV2"]
    blocked: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for t in universe:
        flag = block_fn(t)
        if t.get(event_field) is not None:
            flag = _bool_val(t.get(event_field))
        if flag:
            blocked.append(dict(t))
        else:
            kept.append(dict(t))
    return universe, blocked, kept


def _daily_consistency(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [r for r in daily_rows if r.get("delta_pnl_yen") is not None]
    improved = [r for r in rows if float(r.get("delta_pnl_yen") or 0) >= 0]
    return {
        "days_with_data": len(rows),
        "improved_days": len(improved),
        "improved_day_rate": round(len(improved) / len(rows), 4) if rows else 0.0,
        "total_delta_pnl": round(sum(float(r.get("delta_pnl_yen") or 0) for r in rows), 2),
    }


def _symbol_concentration(symbol_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not symbol_rows:
        return {"top3_abs_share_pct": None, "top_symbols": []}
    ranked = sorted(symbol_rows, key=lambda r: abs(float(r.get("delta_pnl_yen") or 0)), reverse=True)
    total_abs = sum(abs(float(r.get("delta_pnl_yen") or 0)) for r in ranked)
    top3_abs = sum(abs(float(r.get("delta_pnl_yen") or 0)) for r in ranked[:3])
    return {
        "top3_abs_share_pct": round(top3_abs / total_abs * 100.0, 2) if total_abs else None,
        "top_symbols": ranked[:5],
    }


@dataclass
class ShadowReviewRow:
    shadow_id: str
    priority_group: str
    category: str
    evaluable: bool
    decision: str
    rationale: str
    evaluation_method: str
    trade_count: int
    trigger_or_block_count: int
    baseline_pnl_yen: float
    shadow_pnl_yen: float
    delta_pnl_yen: float
    baseline_pf: Optional[float]
    shadow_pf: Optional[float]
    delta_pf: Optional[float]
    baseline_dd_yen: float
    shadow_dd_yen: float
    delta_dd_yen: float
    blocked_winners: int
    blocked_losers: int
    blocked_big_winners: int
    big_winner_blocked_pnl: float
    recent_5d_delta_yen: float
    baseline_stop_hit_rate: Optional[float] = None
    kept_stop_hit_rate: Optional[float] = None
    delta_stop_hit_rate: Optional[float] = None
    baseline_no_progress_rate: Optional[float] = None
    kept_no_progress_rate: Optional[float] = None
    delta_no_progress_rate: Optional[float] = None
    baseline_mfe0_rate: Optional[float] = None
    kept_mfe0_rate: Optional[float] = None
    improved_day_rate: Optional[float] = None
    top3_symbol_share_pct: Optional[float] = None
    mainline_block_pct: Optional[float] = None
    phase657_decision: str = ""
    data_gap: str = ""
    unevaluable_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shadow_id": self.shadow_id,
            "priority_group": self.priority_group,
            "category": self.category,
            "evaluable": self.evaluable,
            "decision": self.decision,
            "rationale": self.rationale,
            "evaluation_method": self.evaluation_method,
            "trade_count": self.trade_count,
            "trigger_or_block_count": self.trigger_or_block_count,
            "baseline_pnl_yen": self.baseline_pnl_yen,
            "shadow_pnl_yen": self.shadow_pnl_yen,
            "delta_pnl_yen": self.delta_pnl_yen,
            "baseline_pf": self.baseline_pf,
            "shadow_pf": self.shadow_pf,
            "delta_pf": self.delta_pf,
            "baseline_dd_yen": self.baseline_dd_yen,
            "shadow_dd_yen": self.shadow_dd_yen,
            "delta_dd_yen": self.delta_dd_yen,
            "blocked_winners": self.blocked_winners,
            "blocked_losers": self.blocked_losers,
            "blocked_big_winners": self.blocked_big_winners,
            "big_winner_blocked_pnl": self.big_winner_blocked_pnl,
            "recent_5d_delta_yen": self.recent_5d_delta_yen,
            "baseline_stop_hit_rate": self.baseline_stop_hit_rate,
            "kept_stop_hit_rate": self.kept_stop_hit_rate,
            "delta_stop_hit_rate": self.delta_stop_hit_rate,
            "baseline_no_progress_rate": self.baseline_no_progress_rate,
            "kept_no_progress_rate": self.kept_no_progress_rate,
            "delta_no_progress_rate": self.delta_no_progress_rate,
            "baseline_mfe0_rate": self.baseline_mfe0_rate,
            "kept_mfe0_rate": self.kept_mfe0_rate,
            "improved_day_rate": self.improved_day_rate,
            "top3_symbol_share_pct": self.top3_symbol_share_pct,
            "mainline_block_pct": self.mainline_block_pct,
            "phase657_decision": self.phase657_decision,
            "data_gap": self.data_gap,
            "unevaluable_reason": self.unevaluable_reason,
        }


def _priority_group(shadow_id: str) -> str:
    if shadow_id in PRIORITY_SHADOW_IDS:
        return "priority"
    if shadow_id in OTHER_RUNTIME_SHADOW_IDS:
        return "other_runtime"
    return "other"


def _is_mfe0(trade: Mapping[str, Any]) -> bool:
    from research.phase631_profit_source_attribution import _num

    mfe = _num(trade.get("peak_mfe_pct"))
    return mfe is not None and float(mfe) <= 0.0


def decide_shadow(
    ev: ShadowEval,
    *,
    shadow_def: Optional[ShadowDef],
    daily_consistency: Mapping[str, Any],
    all_evals: Mapping[str, ShadowEval],
) -> tuple[str, str]:
    sid = ev.shadow_id

    if shadow_def and shadow_def.adopted_mainline:
        return "KEEP", "Already on mainline; shadow logging continues for monitoring."

    if not ev.evaluable:
        if sid in ("extended_entry_shadow", "quality_formula_shadow", "trading_value_shadow_gate"):
            return "KEEP", "Logging-only shadow; low cost observability."
        if sid == "low_liquidity_shadow":
            return "REMOVE", "Disabled in production YAML; remove dead code path."
        return "KEEP", f"Unevaluable on trade replay ({ev.unevaluable_reason or 'no data'})."

    bw = ev.blocked_winners
    bl = ev.rescued_losers
    improved_rate = float(daily_consistency.get("improved_day_rate") or 0)
    recent = float(ev.recent_5d_delta_yen or 0)

    if sid == "board_dynamic_trailing_shadow":
        return "KEEP", "Production EXIT policy; shadow tracks legacy fixed-trail delta."

    if sid in ("loss_acceleration_exit", "board_collapse_profit_exit", "profit_protect_exit"):
        return "REMOVE", "Subsumed by realtime_board_exit_shadow; redundant CPU."

    if ev.delta_pnl_yen < -30000 and recent <= 0:
        return "REMOVE", f"Negative full-period ({ev.delta_pnl_yen:+.0f}) and non-positive recent 5d."

    if bw > bl * 1.15 and ev.delta_pnl_yen < 20000:
        return "REMOVE", f"Blocks more winners than losers ({bw}/{bl}) without sufficient uplift."

    adopt_ok = (
        ev.delta_pnl_yen > 40000
        and (ev.delta_pf or 0) > 0.02
        and bl >= bw
        and float(ev.delta_dd_yen or 0) >= 0
        and recent >= -10000
        and improved_rate >= 0.55
    )

    if sid == "pbv2_flat_band_shadow" and adopt_ok:
        rise5 = all_evals.get("pbv2_rise5_shadow")
        if rise5 and rise5.evaluable and rise5.delta_pnl_yen > 0:
            return (
                "ADOPT",
                f"Flat-band ADOPT candidate (ΔPnL {ev.delta_pnl_yen:+.0f}); prefer over rise5 overlap.",
            )
        return "ADOPT", f"Flat-band ADOPT candidate (ΔPnL {ev.delta_pnl_yen:+.0f}, L/W {bl}/{bw})."

    if sid == "pbv2_rise5_shadow":
        flat = all_evals.get("pbv2_flat_band_shadow")
        if flat and flat.evaluable and flat.delta_pnl_yen >= ev.delta_pnl_yen:
            return "REMOVE", "Redundant with flat-band shadow; flat-band covers rise5 overheat subset."
        if adopt_ok:
            return "ADOPT", f"Rise5 ADOPT candidate (ΔPnL {ev.delta_pnl_yen:+.0f})."
        return "KEEP", "Marginal overlap with flat-band; keep until flat-band mainlined."

    if sid in ("exit_shadow_monitor_t2", "exit_shadow_monitor_t3"):
        if ev.delta_pnl_yen > 80000 and bl >= bw:
            return "ADOPT", f"EXIT {sid} strong uplift (ΔPnL {ev.delta_pnl_yen:+.0f})."
        if ev.delta_pnl_yen > 0:
            return "KEEP", "Positive exit overlay; continue monitor before mainline switch."
        return "REMOVE", "Exit overlay does not improve full-period PnL."

    if sid == "pullback_misread_guard_shadow":
        if ev.delta_pnl_yen > 30000 and bl >= bw:
            return "ADOPT", f"Pullback misread guard helps (ΔPnL {ev.delta_pnl_yen:+.0f})."
        return "KEEP", "Small-scope Dynamic40 counterfactual; keep logging."

    if adopt_ok:
        return "ADOPT", f"Meets adoption thresholds (ΔPnL {ev.delta_pnl_yen:+.0f}, L/W {bl}/{bw})."

    if ev.delta_pnl_yen > 0:
        return "KEEP", f"Positive counterfactual (ΔPnL {ev.delta_pnl_yen:+.0f}); not yet adoption-ready."

    return "KEEP", "Insufficient edge; maintain observability until next review."


def _build_review_row(
    ev: ShadowEval,
    *,
    trades: Sequence[Mapping[str, Any]],
    shadow_defs: Mapping[str, ShadowDef],
    phase657: Mapping[str, str],
    all_evals: Mapping[str, ShadowEval],
) -> ShadowReviewRow:
    daily = _daily_consistency(ev.daily_rows)
    sym = _symbol_concentration(ev.symbol_rows)
    decision, rationale = decide_shadow(
        ev, shadow_def=shadow_defs.get(ev.shadow_id), daily_consistency=daily, all_evals=all_evals
    )

    universe, blocked, kept = _blocked_kept_universe(trades, ev.shadow_id)
    bw_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in blocked if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN)
    blocked_big = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN)

    base_stop = _rate(universe if universe else trades, _is_stop_hit)
    kept_stop = _rate(kept if kept else trades, _is_stop_hit)
    base_np = _rate(universe if universe else trades, _is_no_progress)
    kept_np = _rate(kept if kept else trades, _is_no_progress)
    base_mfe0 = _rate(universe if universe else trades, _is_mfe0)
    kept_mfe0 = _rate(kept if kept else trades, _is_mfe0)

    block_pct = round(len(blocked) / len(universe), 4) if universe else None

    sd = shadow_defs.get(ev.shadow_id)
    return ShadowReviewRow(
        shadow_id=ev.shadow_id,
        priority_group=_priority_group(ev.shadow_id),
        category=ev.category or (sd.category if sd else ""),
        evaluable=ev.evaluable,
        decision=decision,
        rationale=rationale,
        evaluation_method=ev.evaluation_method,
        trade_count=ev.trade_count,
        trigger_or_block_count=ev.trigger_or_block_count,
        baseline_pnl_yen=ev.baseline_pnl_yen,
        shadow_pnl_yen=ev.shadow_pnl_yen,
        delta_pnl_yen=ev.delta_pnl_yen,
        baseline_pf=ev.baseline_pf,
        shadow_pf=ev.shadow_pf,
        delta_pf=ev.delta_pf,
        baseline_dd_yen=ev.baseline_dd_yen,
        shadow_dd_yen=ev.shadow_dd_yen,
        delta_dd_yen=ev.delta_dd_yen,
        blocked_winners=ev.blocked_winners,
        blocked_losers=ev.rescued_losers,
        blocked_big_winners=blocked_big,
        big_winner_blocked_pnl=round(bw_pnl, 2),
        recent_5d_delta_yen=ev.recent_5d_delta_yen,
        baseline_stop_hit_rate=base_stop,
        kept_stop_hit_rate=kept_stop,
        delta_stop_hit_rate=round(kept_stop - base_stop, 4) if kept_stop is not None and base_stop is not None else None,
        baseline_no_progress_rate=base_np,
        kept_no_progress_rate=kept_np,
        delta_no_progress_rate=round(kept_np - base_np, 4) if kept_np is not None and base_np is not None else None,
        baseline_mfe0_rate=base_mfe0,
        kept_mfe0_rate=kept_mfe0,
        improved_day_rate=daily.get("improved_day_rate"),
        top3_symbol_share_pct=sym.get("top3_abs_share_pct"),
        mainline_block_pct=block_pct,
        phase657_decision=phase657.get(ev.shadow_id, ""),
        data_gap=ev.data_gap,
        unevaluable_reason=ev.unevaluable_reason,
    )


def _mandatory_answers(rows: Sequence[ShadowReviewRow]) -> dict[str, Any]:
    by_id = {r.shadow_id: r for r in rows}
    priority = [r for r in rows if r.priority_group == "priority"]
    adopt = [r.shadow_id for r in rows if r.decision == "ADOPT"]
    remove = [r.shadow_id for r in rows if r.decision == "REMOVE"]
    return {
        "1_priority_shadow_decisions": {r.shadow_id: {"decision": r.decision, "rationale": r.rationale} for r in priority},
        "2_adopt_list": adopt,
        "3_remove_list": remove,
        "4_flat_band_vs_rise5": {
            "flat_band": by_id["pbv2_flat_band_shadow"].to_dict() if "pbv2_flat_band_shadow" in by_id else None,
            "rise5": by_id["pbv2_rise5_shadow"].to_dict() if "pbv2_rise5_shadow" in by_id else None,
        },
        "5_exit_t2_t3": {
            "t2": by_id.get("exit_shadow_monitor_t2").to_dict() if "exit_shadow_monitor_t2" in by_id else None,
            "t3": by_id.get("exit_shadow_monitor_t3").to_dict() if "exit_shadow_monitor_t3" in by_id else None,
        },
        "6_new_shadow_gate": {
            "flat_weak_range_shadow": "BLOCKED until this review completes",
            "recommendation": "Complete ADOPT/REMOVE on rise5+flat-band before adding phase667 flat_weak+range reject.",
        },
        "7_portfolio_summary": {
            "adopt_count": len(adopt),
            "remove_count": len(remove),
            "keep_count": sum(1 for r in rows if r.decision == "KEEP"),
        },
    }


def _write_decision_md(*, report: Mapping[str, Any], answers: Mapping[str, Any]) -> None:
    lines = [
        "# Phase668 — Existing Shadow Adoption Review",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## Portfolio decisions",
        "",
        "| Shadow | Group | Decision | ΔPnL | blocked W/L | Rationale |",
        "|--------|-------|----------|------|-------------|-----------|",
    ]
    for r in report.get("review_rows") or []:
        lines.append(
            f"| {r.get('shadow_id')} | {r.get('priority_group')} | **{r.get('decision')}** | "
            f"{r.get('delta_pnl_yen'):+,.0f} | {r.get('blocked_winners')}/{r.get('blocked_losers')} | "
            f"{r.get('rationale')} |"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for key, title in (
        ("1_priority_shadow_decisions", "優先Shadow判定"),
        ("2_adopt_list", "ADOPT一覧"),
        ("3_remove_list", "REMOVE一覧"),
        ("6_new_shadow_gate", "新規Shadowゲート"),
    ):
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"```json\n{json.dumps(answers.get(key), ensure_ascii=False, indent=2)}\n```")
        lines.append("")
    lines.extend(
        [
            "## Constraints",
            "",
            "- 新規 flat_weak+range Shadow 追加禁止（本レビュー完了まで）",
            "- Runtime / YAML 変更なし",
            "- Counterfactual / 整理のみ",
            "",
        ]
    )
    (REPORT_ROOT / "phase668_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(*, skip_slow: bool = True, max_workers: int = MAX_WORKERS) -> dict[str, Any]:
    del max_workers  # phase658 evaluation is single-threaded replay
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    disk_cap_exceeded_at_start = disk_before > DISK_USAGE_MAX_PCT

    repo_root = resolve_kabu_root(NATIVE_ROOT)
    trades, sessions = load_all_full_period_trades(repo_root / "results" / "small_paper")
    trades, sessions = _filter_canonical([dict(t) for t in trades], sessions)
    session_dirs = {
        str(s["session"]): Path(str(s["session_dir"]))
        for s in sessions
        if not _is_push_replay_session(Path(str(s["session_dir"])))
    }
    baseline = _metrics(trades)
    summaries = _discover_summaries_extended()
    shadow_defs = {sd.shadow_id: sd for sd in _registry_definitions() + _research_shadow_defs()}
    ctx = EvalContext(
        trades=trades,
        sessions=sessions,
        session_dirs=session_dirs,
        baseline=baseline,
        summaries=summaries,
        summary_by_session={},
        shadow_defs=shadow_defs,
        skip_slow=skip_slow,
    )
    _enrich_trades_from_events(ctx)
    phase657 = _load_phase657_decisions(repo_root)

    evaluations = [e for e in _evaluate_all_shadows(ctx) if e.shadow_id in RUNTIME_REVIEW_IDS]
    eval_by_id = {e.shadow_id: e for e in evaluations}

    review_rows = [
        _build_review_row(ev, trades=trades, shadow_defs=shadow_defs, phase657=phase657, all_evals=eval_by_id)
        for ev in evaluations
    ]
    review_rows.sort(key=lambda r: (0 if r.priority_group == "priority" else 1, r.shadow_id))

    counterfactual_rows = [r.to_dict() for r in review_rows]
    daily_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    for ev in evaluations:
        daily = _daily_consistency(ev.daily_rows)
        for dr in ev.daily_rows:
            daily_rows.append({**dr, "improved_day_rate": daily.get("improved_day_rate")})
        for sr in ev.symbol_rows:
            sym = _symbol_concentration(ev.symbol_rows)
            symbol_rows.append({**sr, "top3_abs_share_pct": sym.get("top3_abs_share_pct")})

    answers = _mandatory_answers(review_rows)
    disk_after = _disk_usage_pct(NATIVE_ROOT)

    report: dict[str, Any] = {
        "verdict": PHASE668_VERDICT,
        "entry_count": len(trades),
        "trading_day_count": len({t.get("day") for t in trades}),
        "canonical_days": list(CANONICAL_DAYS),
        "baseline_pnl_yen_100": baseline.get("pnl_yen_100"),
        "review_row_count": len(review_rows),
        "adopt": [r.shadow_id for r in review_rows if r.decision == "ADOPT"],
        "keep": [r.shadow_id for r in review_rows if r.decision == "KEEP"],
        "remove": [r.shadow_id for r in review_rows if r.decision == "REMOVE"],
        "mandatory_answers": answers,
        "review_rows": counterfactual_rows,
        "new_shadow_blocked": True,
        "new_shadow_note": "flat_weak+range reject shadow must not be added until this review is acted on",
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    summary_cols = list(ShadowReviewRow("", "", "", False, "", "", "", 0, 0, 0, 0, 0, None, None, None, 0, 0, 0, 0, 0, 0, 0, 0).to_dict().keys())
    _write_csv(REPORT_ROOT / "phase668_shadow_adoption_summary.csv", summary_cols, counterfactual_rows)
    _write_csv(
        REPORT_ROOT / "phase668_shadow_adoption_counterfactual.csv",
        summary_cols,
        counterfactual_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase668_shadow_adoption_daily.csv",
        ["shadow_id", "day", "period", "baseline_pnl_yen", "shadow_pnl_yen", "delta_pnl_yen", "improved_day_rate"],
        daily_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase668_shadow_adoption_symbol.csv",
        ["shadow_id", "symbol", "delta_pnl_yen", "top3_abs_share_pct"],
        symbol_rows,
    )
    (REPORT_ROOT / "phase668_shadow_adoption_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_ROOT / "phase668_disk_usage_report.json").write_text(
        json.dumps(
            {
                "disk_usage_before_pct": round(disk_before, 2),
                "disk_usage_after_pct": round(disk_after, 2),
                "disk_cap_pct": DISK_USAGE_MAX_PCT,
                "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
                "temp_files_created": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_decision_md(report=report, answers=answers)
    return report


if __name__ == "__main__":
    result = run_audit()
    print(json.dumps({"verdict": result["verdict"], "adopt": result["adopt"], "remove": result["remove"]}, ensure_ascii=False))
