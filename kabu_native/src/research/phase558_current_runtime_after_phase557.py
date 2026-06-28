"""
Phase558 — Current Runtime full-period replay after Phase557 stop_low_mfe guard (research only).

Compares Legacy / Phase551 Runtime / Phase558 Latest Runtime (+ G554_022).
No Runtime changes. Evaluation only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase484_stop_low_mfe_feature_discovery import _load_day_event_snaps
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _latest_live_day,
)
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
    _resolved_exit_reason,
)
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase546_entry_cluster_shadow_replay import (
    _is_rejected,
    _merge_dataset,
    _metrics_from_trades,
    _trade_key,
)
from research.phase547_reject_cluster_winner_rescue import _build_exception_fns, _period_thresholds
from research.phase551_current_runtime_full_period_replay import (
    E4_THRESHOLD,
    V6_SPEC,
    _cap_extension_metrics,
    _combine_metrics,
    _entry_quality_block,
    _equity_sim_rows,
    _is_or_trade,
    _iter_calendar_days,
    _reentry_rsi_block,
)
from research.phase554_stop_low_mfe_entry_quality_feature_study import (
    _enrich_phase554,
    _feature_value,
    _is_stop_low_mfe_554,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE558_VERDICT = "phase558_current_runtime_after_phase557_done"
PERIOD_MIN = "20260616"
PERIOD_EXTENDED_START = "20260529"
PERIOD_DEFAULT_END = "20260625"
SLM_THRESHOLD = 0.009
SLM_FEATURE = "volume_acceleration_5m"

VariantSpec = tuple[str, str, bool, bool, bool, bool, bool, bool]

VARIANTS: tuple[VariantSpec, ...] = (
    ("C_legacy", "Legacy baseline (PBv2, no OR, no guards)", False, False, False, False, False, False),
    (
        "B_phase551",
        "Phase551 Current Runtime (OR+guards+ClusterGuard V6+E4, no SLM)",
        True,
        True,
        True,
        True,
        True,
        False,
    ),
    (
        "D_phase558",
        "Phase558 Latest Runtime (+ stop_low_mfe G554_022)",
        True,
        True,
        True,
        True,
        True,
        True,
    ),
)

COMPARISON_FIELDS = [
    "variant_id",
    "label",
    "period",
    "live_period",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_drawdown_yen_100",
    "mfe0_count",
    "stop_low_mfe_count",
    "no_progress_count",
    "pbv2_trades",
    "pbv2_pnl_yen_100",
    "or_trades",
    "or_pnl_yen_100",
    "cluster_guard_reject_count",
    "cluster_guard_exception_count",
    "stop_low_mfe_guard_reject_count",
    "stop_low_mfe_guard_missing_count",
    "blocked_winner_count",
    "blocked_big_winner_count",
    "live_trades",
    "live_pnl_yen_100",
    "live_profit_factor",
    "live_max_drawdown_yen_100",
    "cap_extension_pnl_yen_100",
    "cap_extension_trades",
]

DAILY_FIELDS = [
    "day",
    "variant_id",
    "daily_pnl_yen_100",
    "daily_pf",
    "daily_trades",
    "daily_mfe0",
    "daily_stop_low_mfe",
    "daily_cluster_reject",
    "daily_slm_reject",
    "daily_pbv2_pnl",
    "daily_or_pnl",
]

EQUITY_FIELDS = [
    "variant_id",
    "mode",
    "initial_equity_yen",
    "final_equity_yen",
    "total_return_pct",
    "max_drawdown_yen",
    "max_drawdown_pct",
    "trade_skip_count_due_to_capital",
    "capital_utilization",
    "accepted_trades",
]

GUARD_CONTRIBUTION_FIELDS = [
    "component",
    "delta_pnl_yen_100",
    "blocked_trades",
    "blocked_winners",
    "blocked_big_winners",
    "notes",
]


def _is_big_winner(row: Mapping[str, Any]) -> bool:
    return _is_winner(row) and _mfe_pct(row) >= BIG_WINNER_MFE_PCT


def _slm_guard_reject(row: Mapping[str, Any], *, missing_policy: str = "pass") -> bool:
    if _is_or_trade(row):
        return False
    v = _feature_value(row, SLM_FEATURE)
    if v is None:
        return missing_policy == "reject"
    return v > SLM_THRESHOLD


def _evaluate_live_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    include_or: bool,
    reentry_rsi: bool,
    entry_quality: bool,
    cluster_guard: bool,
    cluster_exception: bool,
    stop_low_mfe_guard: bool,
    bar_cache: Mapping,
    thresholds: Mapping[str, float],
    missing_policy: str = "pass",
) -> dict[str, Any]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))

    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    cluster_reject = 0
    cluster_exception_count = 0
    slm_reject = 0
    slm_missing = 0
    slm_blocked_rows: list[dict[str, Any]] = []

    for sym in sorted(by_sym):
        seq = sorted(
            by_sym[sym],
            key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
        )
        prev: Optional[dict[str, Any]] = None
        for trade in seq:
            row = dict(trade)
            reasons: list[str] = []
            if not include_or and _is_or_trade(row):
                reasons.append("legacy_no_or")
            if reentry_rsi and _reentry_rsi_block(row, prev, bar_cache):
                reasons.append("reentry_rsi_guard")
            feats = {
                "spread": row.get("spread_bps") or row.get("spread"),
                "update_count_before_entry": row.get("update_count_before_entry"),
            }
            if entry_quality and _entry_quality_block(feats):
                reasons.append("entry_quality_guard")

            if (
                not reasons
                and cluster_guard
                and not _is_or_trade(row)
                and _is_rejected(row, V6_SPEC)
            ):
                rescued = cluster_exception and _build_exception_fns(thresholds)["E4"][2](row)
                if rescued:
                    row["cluster_guard_status"] = "EXCEPTION"
                    cluster_exception_count += 1
                else:
                    reasons.append("entry_cluster_guard")
                    cluster_reject += 1

            if not reasons and stop_low_mfe_guard and not _is_or_trade(row):
                feat_val = _feature_value(row, SLM_FEATURE)
                if feat_val is None:
                    slm_missing += 1
                    if missing_policy == "reject":
                        reasons.append("stop_low_mfe_guard")
                        slm_reject += 1
                        slm_blocked_rows.append(row)
                elif feat_val > SLM_THRESHOLD:
                    reasons.append("stop_low_mfe_guard")
                    slm_reject += 1
                    slm_blocked_rows.append(row)

            if reasons:
                row["block_reasons"] = "|".join(reasons)
                blocked.append(row)
            else:
                if row.get("cluster_guard_status") != "EXCEPTION" and cluster_guard:
                    row["cluster_guard_status"] = "PASSED"
                accepted.append(row)
                prev = row

    baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in trades), 2)
    met = _metrics_from_trades(accepted, blocked, baseline_pnl=baseline_pnl, baseline_trades=len(trades))
    pbv2_acc = [t for t in accepted if not _is_or_trade(t)]
    or_acc = [t for t in accepted if _is_or_trade(t)]
    pbv2_pnls = [_num(t.get("pnl_yen_100")) for t in pbv2_acc]
    or_pnls = [_num(t.get("pnl_yen_100")) for t in or_acc]
    slm_blocked_pnls = [_num(t.get("pnl_yen_100")) for t in slm_blocked_rows]

    return {
        **{k: v for k, v in met.items() if not str(k).startswith("_")},
        "stop_hit_count": sum(1 for t in accepted if _resolved_exit_reason(t) == "stop_hit"),
        "pbv2_trades": len(pbv2_acc),
        "pbv2_pnl_yen_100": round(sum(pbv2_pnls), 2),
        "pbv2_profit_factor": _pf(pbv2_pnls),
        "or_trades": len(or_acc),
        "or_pnl_yen_100": round(sum(or_pnls), 2),
        "or_profit_factor": _pf(or_pnls),
        "cluster_guard_reject_count": cluster_reject,
        "cluster_guard_exception_count": cluster_exception_count,
        "stop_low_mfe_guard_reject_count": slm_reject,
        "stop_low_mfe_guard_missing_count": slm_missing,
        "stop_low_mfe_guard_blocked_loss": round(
            sum(-p for p in slm_blocked_pnls if p < 0), 2
        ),
        "stop_low_mfe_guard_blocked_winner": round(
            sum(p for p in slm_blocked_pnls if p > 0), 2
        ),
        "blocked_winner_count": sum(1 for t in slm_blocked_rows if _is_winner(t)),
        "blocked_big_winner_count": sum(1 for t in slm_blocked_rows if _is_big_winner(t)),
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe_554(t)),
        "no_progress_count": sum(1 for t in accepted if _is_no_progress(t)),
        "mfe0_count": sum(1 for t in accepted if _is_mfe0(t)),
        "_accepted": accepted,
        "_blocked": blocked,
        "_slm_blocked": slm_blocked_rows,
    }


def _daily_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    eval_fn: Callable[[Sequence[Mapping[str, Any]]], dict[str, Any]],
) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")[:8]].append(dict(t))
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        ev = eval_fn(by_day[day])
        acc = ev.get("_accepted") or []
        pbv2 = [t for t in acc if not _is_or_trade(t)]
        or_t = [t for t in acc if _is_or_trade(t)]
        rows.append(
            {
                "day": day,
                "variant_id": variant_id,
                "daily_pnl_yen_100": ev.get("pnl_yen_100"),
                "daily_pf": ev.get("profit_factor"),
                "daily_trades": ev.get("trades"),
                "daily_mfe0": ev.get("mfe0_count"),
                "daily_stop_low_mfe": ev.get("stop_low_mfe_count"),
                "daily_cluster_reject": ev.get("cluster_guard_reject_count"),
                "daily_slm_reject": ev.get("stop_low_mfe_guard_reject_count"),
                "daily_pbv2_pnl": round(sum(_num(t.get("pnl_yen_100")) for t in pbv2), 2),
                "daily_or_pnl": round(sum(_num(t.get("pnl_yen_100")) for t in or_t), 2),
            }
        )
    return rows


def _guard_contribution_rows(
    live: Mapping[str, Mapping[str, Any]],
    combined: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    legacy = live.get("C_legacy", {})
    p551 = live.get("B_phase551", {})
    p558 = live.get("D_phase558", {})
    fc = combined.get("C_legacy", {})
    fb = combined.get("B_phase551", {})
    fd = combined.get("D_phase558", {})
    slm_blocked = p558.get("_slm_blocked") or []
    return [
        {
            "component": "OR_plus_guards_vs_legacy_live",
            "delta_pnl_yen_100": round(_num(p551.get("pnl_yen_100")) - _num(legacy.get("pnl_yen_100")), 2),
            "blocked_trades": 0,
            "blocked_winners": 0,
            "blocked_big_winners": 0,
            "notes": "Phase551 vs Legacy live window",
        },
        {
            "component": "ClusterGuard_net_live",
            "delta_pnl_yen_100": round(_num(p551.get("pnl_yen_100")) - _num(legacy.get("pnl_yen_100")), 2),
            "blocked_trades": p551.get("cluster_guard_reject_count"),
            "blocked_winners": 0,
            "blocked_big_winners": 0,
            "notes": "ClusterGuard+E4 on accepted set (Phase551)",
        },
        {
            "component": "stop_low_mfe_guard_live",
            "delta_pnl_yen_100": round(_num(p558.get("pnl_yen_100")) - _num(p551.get("pnl_yen_100")), 2),
            "blocked_trades": p558.get("stop_low_mfe_guard_reject_count"),
            "blocked_winners": p558.get("blocked_winner_count"),
            "blocked_big_winners": p558.get("blocked_big_winner_count"),
            "notes": "Phase558 vs Phase551 live window (G554_022)",
        },
        {
            "component": "stop_low_mfe_guard_full_period",
            "delta_pnl_yen_100": round(_num(fd.get("pnl_yen_100")) - _num(fb.get("pnl_yen_100")), 2),
            "blocked_trades": p558.get("stop_low_mfe_guard_reject_count"),
            "blocked_winners": p558.get("blocked_winner_count"),
            "blocked_big_winners": p558.get("blocked_big_winner_count"),
            "notes": "Combined CAP extension + live",
        },
        {
            "component": "OR_unchanged_by_slm",
            "delta_pnl_yen_100": round(_num(p558.get("or_pnl_yen_100")) - _num(p551.get("or_pnl_yen_100")), 2),
            "blocked_trades": 0,
            "blocked_winners": 0,
            "blocked_big_winners": 0,
            "notes": "OR PnL delta Phase558 vs Phase551 (expect 0)",
        },
        {
            "component": "slm_blocked_net_shadow",
            "delta_pnl_yen_100": round(-sum(_num(t.get("pnl_yen_100")) for t in slm_blocked), 2),
            "blocked_trades": len(slm_blocked),
            "blocked_winners": sum(1 for t in slm_blocked if _is_winner(t)),
            "blocked_big_winners": sum(1 for t in slm_blocked if _is_big_winner(t)),
            "notes": "PnL of trades blocked by SLM guard only",
        },
    ]


def _mandatory_answers(
    combined: Mapping[str, Mapping[str, Any]],
    live: Mapping[str, Mapping[str, Any]],
    equity: Sequence[Mapping[str, Any]],
    guard_contrib: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    latest_full = combined.get("D_phase558", {})
    p551_full = combined.get("B_phase551", {})
    latest_live = live.get("D_phase558", {})
    p551_live = live.get("B_phase551", {})
    slm_row = next((r for r in guard_contrib if r.get("component") == "stop_low_mfe_guard_live"), {})
    or_row = next((r for r in guard_contrib if r.get("component") == "OR_unchanged_by_slm"), {})

    def _eq(variant: str, initial: int) -> dict[str, Any]:
        fixed = next(
            (
                r
                for r in equity
                if r.get("variant_id") == variant
                and r.get("mode") == "fixed_100_shares"
                and int(r.get("initial_equity_yen") or 0) == initial
            ),
            {},
        )
        cap20 = next(
            (
                r
                for r in equity
                if r.get("variant_id") == variant
                and r.get("mode") == "max_capital_20pct"
                and int(r.get("initial_equity_yen") or 0) == initial
            ),
            {},
        )
        return {"fixed_100": fixed, "max_capital_20pct": cap20}

    eq1 = _eq("D_phase558", 1_000_000)
    eq3 = _eq("D_phase558", 3_000_000)
    eq5 = _eq("D_phase558", 5_000_000)

    slm_delta = _num(slm_row.get("delta_pnl_yen_100"))
    blocked_big = int(latest_live.get("blocked_big_winner_count") or 0)
    improved = _num(latest_full.get("pnl_yen_100")) > _num(p551_full.get("pnl_yen_100"))

    return {
        "1_latest_runtime_full_period_pnl": latest_full.get("pnl_yen_100"),
        "2_latest_runtime_pf": latest_full.get("profit_factor"),
        "3_latest_runtime_maxDD": latest_full.get("max_drawdown_yen_100"),
        "4_improved_vs_phase551": improved,
        "4_delta_vs_phase551_full": round(
            _num(latest_full.get("pnl_yen_100")) - _num(p551_full.get("pnl_yen_100")), 2
        ),
        "5_slm_guard_contributed": slm_delta > 0,
        "5_slm_guard_delta_live": slm_delta,
        "6_big_winner_over_cut": blocked_big > 3,
        "6_blocked_big_winner_count": blocked_big,
        "6_blocked_winner_count": latest_live.get("blocked_winner_count"),
        "7_or_unaffected": _num(or_row.get("delta_pnl_yen_100")) == 0.0,
        "7_or_pnl_phase558": latest_live.get("or_pnl_yen_100"),
        "7_or_pnl_phase551": p551_live.get("or_pnl_yen_100"),
        "8_equity_1M": eq1,
        "9_equity_3M": eq3,
        "10_equity_5M": eq5,
        "11_runtime_fixed_ok": improved and blocked_big <= 3,
        "12_next_priority": (
            "monitor_live_slm_missing_rate_and_blocked_big_winner"
            if improved and blocked_big <= 1
            else "review_slm_threshold_if_big_winner_cut_rises"
            if blocked_big > 1
            else "hold_runtime_review_after_more_live_days"
        ),
        "live_window_latest": {
            "trades": latest_live.get("trades"),
            "pnl": latest_live.get("pnl_yen_100"),
            "pf": latest_live.get("profit_factor"),
            "slm_reject": latest_live.get("stop_low_mfe_guard_reject_count"),
            "slm_missing": latest_live.get("stop_low_mfe_guard_missing_count"),
        },
    }


@dataclass
class Phase558Job:
    repo_root: Path
    period_start: str = PERIOD_MIN
    period_end: str = PERIOD_DEFAULT_END
    extended_start: str = PERIOD_EXTENDED_START

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        reports = resolve_reports_dir(repo)
        kabu = resolve_kabu_root(repo)
        end = min(self.period_end, _latest_live_day(repo))
        live_start = max(self.period_start, PERIOD_START_LIVE)
        cap_end = (
            datetime.strptime(live_start, "%Y%m%d") - timedelta(days=1)
        ).strftime("%Y%m%d")

        cluster_rows = _merge_dataset(reports)
        cluster_by_key = {_trade_key(r): dict(r) for r in cluster_rows}
        thresholds = _period_thresholds(cluster_rows)
        thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

        days = [d for d in _iter_calendar_days(live_start, end) if d >= PERIOD_START_LIVE]
        live_trades: list[dict[str, Any]] = []
        for day in days:
            for t in _load_canonical_trades_for_day(repo, day, all_sessions=True):
                key = _trade_key(t)
                merged = {**dict(t), **cluster_by_key.get(key, {})}
                merged["day"] = day
                if merged.get("liquidity_burst") in (None, "") and cluster_by_key.get(key):
                    merged["liquidity_burst"] = cluster_by_key[key].get("liquidity_burst")
                live_trades.append(merged)

        if not live_trades:
            raise RuntimeError(f"No live trades for Phase558 {live_start}–{end}")

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
        price_idx = _build_price_index_to(kabu, period_end=end)
        bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)
        from research.phase518_day_high_winner_loser_separation import _build_micro_lookup

        micro = _build_micro_lookup(live_trades)
        board_snaps_by_day = {day: _load_day_event_snaps(kabu, day) for day in days}
        enriched = _enrich_phase554(
            live_trades,
            bar_cache=bar_cache,
            micro_lookup=micro,
            board_snaps_by_day=board_snaps_by_day,
        )

        live_results: dict[str, dict[str, Any]] = {}
        combined_results: dict[str, dict[str, Any]] = {}
        comparison_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        equity_summary: list[dict[str, Any]] = []

        cap_cache: dict[bool, dict[str, Any]] = {}

        for vid, label, inc_or, reentry, eq_guard, cg, exc, slm in VARIANTS:

            def _eval(batch: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
                return _evaluate_live_trades(
                    batch,
                    include_or=inc_or,
                    reentry_rsi=reentry,
                    entry_quality=eq_guard,
                    cluster_guard=cg,
                    cluster_exception=exc,
                    stop_low_mfe_guard=slm,
                    bar_cache=bar_cache,
                    thresholds=thresholds,
                    missing_policy="pass",
                )

            live_ev = _eval(enriched)
            live_results[vid] = live_ev

            if inc_or not in cap_cache:
                cap_cache[inc_or] = _cap_extension_metrics(
                    repo,
                    period_start=self.extended_start,
                    period_end=cap_end,
                    include_or=inc_or,
                )
            cap_ev = cap_cache[inc_or]
            comb = _combine_metrics(live_ev, cap_ev)
            combined_results[vid] = {**live_ev, **comb}

            comparison_rows.append(
                {
                    "variant_id": vid,
                    "label": label,
                    "period": f"{self.extended_start}-{end}",
                    "live_period": f"{live_start}-{end}",
                    "trades": comb.get("trades"),
                    "pnl_yen_100": comb.get("pnl_yen_100"),
                    "profit_factor": comb.get("profit_factor"),
                    "win_rate": comb.get("win_rate"),
                    "max_drawdown_yen_100": comb.get("max_drawdown_yen_100"),
                    "mfe0_count": live_ev.get("mfe0_count"),
                    "stop_low_mfe_count": live_ev.get("stop_low_mfe_count"),
                    "no_progress_count": live_ev.get("no_progress_count"),
                    "pbv2_trades": live_ev.get("pbv2_trades"),
                    "pbv2_pnl_yen_100": live_ev.get("pbv2_pnl_yen_100"),
                    "or_trades": live_ev.get("or_trades"),
                    "or_pnl_yen_100": live_ev.get("or_pnl_yen_100"),
                    "cluster_guard_reject_count": live_ev.get("cluster_guard_reject_count"),
                    "cluster_guard_exception_count": live_ev.get("cluster_guard_exception_count"),
                    "stop_low_mfe_guard_reject_count": live_ev.get("stop_low_mfe_guard_reject_count"),
                    "stop_low_mfe_guard_missing_count": live_ev.get("stop_low_mfe_guard_missing_count"),
                    "blocked_winner_count": live_ev.get("blocked_winner_count"),
                    "blocked_big_winner_count": live_ev.get("blocked_big_winner_count"),
                    "live_trades": live_ev.get("trades"),
                    "live_pnl_yen_100": live_ev.get("pnl_yen_100"),
                    "live_profit_factor": live_ev.get("profit_factor"),
                    "live_max_drawdown_yen_100": live_ev.get("max_drawdown_yen_100"),
                    "cap_extension_pnl_yen_100": cap_ev.get("pnl_yen_100"),
                    "cap_extension_trades": cap_ev.get("trades"),
                }
            )

            daily_rows.extend(_daily_rows(enriched, variant_id=vid, eval_fn=_eval))
            es, _ = _equity_sim_rows(live_ev.get("_accepted") or [], variant_id=vid)
            equity_summary.extend(es)

        guard_contrib = _guard_contribution_rows(live_results, combined_results)
        mandatory = _mandatory_answers(
            combined_results,
            live_results,
            equity_summary,
            guard_contrib,
        )

        serializable_live = {
            k: {kk: vv for kk, vv in v.items() if not str(kk).startswith("_")}
            for k, v in live_results.items()
        }

        return {
            "verdict": PHASE558_VERDICT,
            "generated_at": _now_iso(),
            "period_live": f"{live_start}-{end}",
            "period_full": f"{self.extended_start}-{end}",
            "live_trade_count": len(enriched),
            "comparison": comparison_rows,
            "daily": daily_rows,
            "equity_summary": equity_summary,
            "guard_contribution": guard_contrib,
            "mandatory_answers": mandatory,
            "live_results_summary": serializable_live,
            "production_yaml": "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
            "runtime_config": {
                "or_overlay": True,
                "cap_pbv2": 4,
                "cap_or": 1,
                "cap_total": 5,
                "reentry_rsi_guard": True,
                "entry_quality_guard": True,
                "cluster_guard": True,
                "stop_low_mfe_guard": True,
                "stop_low_mfe_threshold": SLM_THRESHOLD,
                "stop_low_mfe_missing_policy": "pass",
                "stop_low_mfe_pbv2_only": True,
            },
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        docs = kabu / "docs" / "operations" / "phase558_current_runtime_after_phase557.md"
        paths = {
            "comparison": reports / "phase558_runtime_comparison_summary.csv",
            "daily": reports / "phase558_runtime_daily.csv",
            "equity": reports / "phase558_equity_simulation.csv",
            "guard_contribution": reports / "phase558_guard_contribution.csv",
            "report": reports / "phase558_report.json",
            "docs": docs,
        }
        _write_csv(paths["comparison"], COMPARISON_FIELDS, result.get("comparison") or [])
        _write_csv(paths["daily"], DAILY_FIELDS, result.get("daily") or [])
        _write_csv(paths["equity"], EQUITY_FIELDS, result.get("equity_summary") or [])
        _write_csv(paths["guard_contribution"], GUARD_CONTRIBUTION_FIELDS, result.get("guard_contribution") or [])

        report_payload = {
            k: v
            for k, v in result.items()
            if k not in ("comparison", "daily", "equity_summary", "guard_contribution")
        }
        paths["report"].write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        ma = result.get("mandatory_answers") or {}
        comp = {r.get("variant_id"): r for r in (result.get("comparison") or [])}
        gc = result.get("guard_contribution") or []

        def _fmt_equity(label: str, payload: Mapping[str, Any]) -> str:
            fixed = payload.get("fixed_100") or {}
            cap = payload.get("max_capital_20pct") or {}
            return (
                f"{label}: fixed_100 final={fixed.get('final_equity_yen')} "
                f"ret={fixed.get('total_return_pct')}% maxDD={fixed.get('max_drawdown_yen')}; "
                f"max_capital_20pct final={cap.get('final_equity_yen')} "
                f"ret={cap.get('total_return_pct')}% skips={cap.get('trade_skip_count_due_to_capital')}"
            )

        lines = [
            "# Phase558 — Current Runtime Replay after Phase557",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Generated:** {result.get('generated_at')}",
            f"**Live period:** {result.get('period_live')}",
            f"**Full period:** {result.get('period_full')}",
            "",
            "## Variants",
            "",
            "- **C_legacy:** PBv2 only, no OR, no guards.",
            "- **B_phase551:** Phase551 runtime (OR + ReEntry RSI + Entry Quality + ClusterGuard V6+E4).",
            "- **D_phase558:** Phase558 latest (+ stop_low_mfe G554_022, threshold 0.009, missing→pass, PBv2 only).",
            "",
            "## Mandatory answers",
            "",
            f"1. **Latest full-period PnL:** {ma.get('1_latest_runtime_full_period_pnl')} yen",
            f"2. **Latest PF:** {ma.get('2_latest_runtime_pf')}",
            f"3. **Latest maxDD:** {ma.get('3_latest_runtime_maxDD')} yen",
            f"4. **Improved vs Phase551:** {ma.get('4_improved_vs_phase551')} (delta {ma.get('4_delta_vs_phase551_full')})",
            f"5. **SLM guard contributed:** {ma.get('5_slm_guard_contributed')} (live delta {ma.get('5_slm_guard_delta_live')})",
            f"6. **Big winner over-cut:** {ma.get('6_big_winner_over_cut')} (blocked big={ma.get('6_blocked_big_winner_count')})",
            f"7. **OR unaffected:** {ma.get('7_or_unaffected')}",
            f"8. **Equity @1M:** {_fmt_equity('1M', ma.get('8_equity_1M') or {})}",
            f"9. **Equity @3M:** {_fmt_equity('3M', ma.get('9_equity_3M') or {})}",
            f"10. **Equity @5M:** {_fmt_equity('5M', ma.get('10_equity_5M') or {})}",
            f"11. **Runtime fixed OK:** {ma.get('11_runtime_fixed_ok')}",
            f"12. **Next priority:** {ma.get('12_next_priority')}",
            "",
            "## Comparison (full period)",
            "",
            "| Variant | Trades | PnL | PF | maxDD | SLM reject | blocked big |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for vid in ("C_legacy", "B_phase551", "D_phase558"):
            r = comp.get(vid) or {}
            lines.append(
                f"| {vid} | {r.get('trades')} | {r.get('pnl_yen_100')} | "
                f"{r.get('profit_factor')} | {r.get('max_drawdown_yen_100')} | "
                f"{r.get('stop_low_mfe_guard_reject_count', 0)} | "
                f"{r.get('blocked_big_winner_count', 0)} |"
            )
        lines.extend(["", "## Guard contribution", ""])
        for row in gc:
            lines.append(
                f"- **{row.get('component')}:** delta={row.get('delta_pnl_yen_100')} "
                f"blocked={row.get('blocked_trades')} — {row.get('notes')}"
            )
        lines.extend(
            [
                "",
                "## Outputs",
                "",
                "- `results/reports/phase558_runtime_comparison_summary.csv`",
                "- `results/reports/phase558_runtime_daily.csv`",
                "- `results/reports/phase558_equity_simulation.csv`",
                "- `results/reports/phase558_guard_contribution.csv`",
                "- `results/reports/phase558_report.json`",
            ]
        )
        docs.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
