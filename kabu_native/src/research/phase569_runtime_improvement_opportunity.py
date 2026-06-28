"""
Phase569 — Runtime improvement opportunity mining (research only).

Ranks improvement headroom across ENTRY / EXIT / CAP / Capital / Universe / sizing
on Phase558 latest Runtime accepted + rejected trades.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase484_stop_low_mfe_feature_discovery import _load_day_event_snaps
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _latest_live_day,
)
from research.phase533_or_profit_source_audit import _num
from research.phase535_or_cap_reality_validation import _cap_scenarios, _simulate_cap_audited
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
    _resolved_exit_reason,
)
from research.phase546_entry_cluster_shadow_replay import _merge_dataset, _trade_key
from research.phase547_reject_cluster_winner_rescue import _period_thresholds
from research.phase551_current_runtime_full_period_replay import E4_THRESHOLD, _iter_calendar_days, _is_or_trade
from research.phase554_stop_low_mfe_entry_quality_feature_study import _enrich_phase554, _is_stop_low_mfe_554
from research.phase558_current_runtime_after_phase557 import _evaluate_live_trades
from research.phase561_trailing_shadow_validation import _load_full_period_accepted
from research.phase566_position_sizing_optimization import MIN_LOT, _prepare_trades, simulate_sizing_policy
from research.phase567_capital_requirement_optimization import _unlimited_pnl
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE569_VERDICT = "phase569_runtime_improvement_opportunity_mining_done"
FULL_START = "20260529"

IMPROVEMENT_RANKING_FIELDS = [
    "rank",
    "category",
    "subcategory",
    "opportunity_yen",
    "opportunity_yen_signed",
    "direction",
    "trade_count",
    "notes",
    "runtime_change_candidate",
]

REJECT_OPPORTUNITY_FIELDS = [
    "reject_reason",
    "reject_category",
    "trade_count",
    "counterfactual_pnl_yen",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen",
    "winner_count",
    "loser_count",
    "big_winner_count",
]

LOSS_ATTRIBUTION_FIELDS = [
    "loss_cause",
    "improvement_bucket",
    "trade_count",
    "total_loss_yen",
    "contribution_pct",
    "avg_loss_yen",
    "avg_mfe_pct",
    "exit_reason_top",
]

OPPORTUNITY_LOSS_FIELDS = [
    "category",
    "subcategory",
    "opportunity_yen",
    "opportunity_yen_signed",
    "trade_count",
    "method",
    "notes",
]

REJECT_CATEGORY_MAP = {
    "entry_cluster_guard": "ENTRY",
    "stop_low_mfe_guard": "ENTRY",
    "reentry_rsi_guard": "ENTRY",
    "entry_quality_guard": "ENTRY",
    "legacy_no_or": "ENTRY",
    "cap_full": "CAP",
    "or_pool_full": "CAP",
    "pbv2_pool_full": "CAP",
    "same_symbol_open": "CAP",
    "overlap_replaced": "CAP",
}


def _primary_reject_reason(row: Mapping[str, Any]) -> str:
    raw = str(row.get("reject_reason") or row.get("block_reasons") or "unknown")
    if "|" in raw:
        return raw.split("|")[0].strip()
    return raw.strip() or "unknown"


def _reject_category(reason: str) -> str:
    return REJECT_CATEGORY_MAP.get(reason, "OTHER")


def _cohort_stats(pnls: Sequence[float]) -> dict[str, Any]:
    if not pnls:
        return {"profit_factor": 0.0, "win_rate": 0.0, "avg_pnl_yen": 0.0}
    return {
        "profit_factor": _pf(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
        "avg_pnl_yen": round(statistics.mean(pnls), 2),
    }


def _realized_pnl_pct(trade: Mapping[str, Any]) -> float:
    v = _float(trade.get("pnl_pct"))
    if v is not None:
        return v
    ep = _num(trade.get("entry_price"))
    if ep <= 0:
        return 0.0
    return _num(trade.get("pnl_yen_100")) / ep


def _exit_opportunity_yen(trade: Mapping[str, Any]) -> float:
    ep = _num(trade.get("entry_price"))
    if ep <= 0:
        return 0.0
    mfe = _mfe_pct(trade)
    realized = _realized_pnl_pct(trade)
    opp_pct = max(0.0, mfe - realized)
    return round(ep * opp_pct / 100.0 * MIN_LOT, 2)


def _classify_loss(trade: Mapping[str, Any]) -> tuple[str, str]:
    reason = _resolved_exit_reason(trade)
    pnl = _num(trade.get("pnl_yen_100"))
    mfe = _mfe_pct(trade)
    if pnl >= 0:
        return "not_loss", "N/A"
    if reason == "stop_hit":
        return "stop_hit", "EXIT"
    if _is_no_progress(trade):
        return "no_progress_exit", "EXIT"
    if _is_mfe0(trade):
        return "mfe0_bad_entry", "ENTRY"
    if _is_stop_low_mfe_554(trade):
        return "stop_low_mfe_pattern", "ENTRY"
    if reason in ("trailing_mfe", "trailing_mfe_exit", "trailing"):
        if mfe >= 1.0:
            return "trailing_giveback", "EXIT"
        return "trailing_weak_mfe", "EXIT"
    if reason == "overlap_replaced":
        return "overlap_replaced", "CAP"
    if reason == "session_close":
        return "session_close", "EXIT"
    if mfe < 0.3:
        return "immediate_adverse", "ENTRY"
    return "other_loss", "OTHER"


def _load_cap_rejects(
    repo: Path,
    *,
    period_start: str,
    period_end: str,
    accepted_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    from research.phase488_current_runtime_replay import _filter_period
    from research.phase507_classic_strategy_battle import _universe_symbols
    from research.phase509_t15_t13_signal_audit import _build_bar_cache
    from research.phase516_pbv2_best_classical_overlay import (
        OVERLAY_DEFS,
        _merge_or_candidates,
        _pbv2_precomputed_candidates,
        _prepare_runtime_env,
        _scan_overlay_day,
    )

    replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(repo)
    pool = _filter_period(replay_pool, start=period_start, end=period_end)
    if not pool:
        return []

    pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
    pbv2_candidates = [
        t for t in pbv2_candidates if period_start <= str(t.get("day") or "")[:8] <= period_end
    ]
    kabu = resolve_kabu_root(repo)
    price_idx = _build_price_index_to(kabu, period_end=period_end)
    bar_cache, days = _build_bar_cache(repo)
    days_f = [d for d in days if period_start <= d <= period_end]
    universe = _universe_symbols(pool)
    overlay_def = OVERLAY_DEFS["O_R003"]
    overlay_all: list[dict[str, Any]] = []
    for day in days_f:
        overlay_all.extend(
            _scan_overlay_day(
                overlay_def,
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )
        )
    candidates = _merge_or_candidates(
        pbv2_candidates,
        overlay_all,
        bar_cache=bar_cache,
        overlay=overlay_def,
        guard_c_block=guard_c_block,
    )
    scenario = next(s for s in _cap_scenarios() if s.scenario_id == "CAP_SPLIT_4_1")
    sim = _simulate_cap_audited(candidates, scenario=scenario)
    cand_by_pk = {_position_key(t): t for t in candidates}

    rejects: list[dict[str, Any]] = []
    for row in sim.entry_audit:
        if row.accepted:
            continue
        trade = dict(cand_by_pk.get(row.position_key, {}))
        key = (str(trade.get("symbol") or row.symbol), str(trade.get("entry_time") or row.entry_time))
        if key in accepted_keys:
            continue
        trade.setdefault("symbol", row.symbol)
        trade.setdefault("entry_time", row.entry_time)
        trade.setdefault("day", row.day)
        trade["reject_reason"] = row.reject_reason or "cap_full"
        trade["pnl_yen_100"] = round(_num(row.hypothetical_pnl), 2)
        rejects.append(trade)
    return rejects


def _load_entry_guard_rejects(
    repo: Path,
    *,
    live_start: str,
    end: str,
) -> list[dict[str, Any]]:
    reports = resolve_reports_dir(repo)
    kabu = resolve_kabu_root(repo)
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
    return list(ev.get("_blocked") or [])


def _build_reject_opportunity_rows(rejects: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_reason: dict[str, list[float]] = defaultdict(list)
    by_reason_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rejects:
        reason = _primary_reject_reason(row)
        pnl = _num(row.get("pnl_yen_100"))
        by_reason[reason].append(pnl)
        by_reason_rows[reason].append(row)

    rows: list[dict[str, Any]] = []
    for reason in sorted(by_reason.keys(), key=lambda r: -abs(sum(by_reason[r]))):
        pnls = by_reason[reason]
        stats = _cohort_stats(pnls)
        rows.append(
            {
                "reject_reason": reason,
                "reject_category": _reject_category(reason),
                "trade_count": len(pnls),
                "counterfactual_pnl_yen": round(sum(pnls), 2),
                "profit_factor": stats["profit_factor"],
                "win_rate": stats["win_rate"],
                "avg_pnl_yen": stats["avg_pnl_yen"],
                "winner_count": sum(1 for p in pnls if p > 0),
                "loser_count": sum(1 for p in pnls if p < 0),
                "big_winner_count": sum(
                    1 for t in by_reason_rows[reason] if _is_winner(t) and _mfe_pct(t) >= 3.0
                ),
            }
        )
    return rows


def _build_loss_attribution(accepted: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    losers = [t for t in accepted if _num(t.get("pnl_yen_100")) < 0]
    total_loss = sum(_num(t.get("pnl_yen_100")) for t in losers) or -1.0
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_cat: dict[str, str] = {}
    for t in losers:
        cause, cat = _classify_loss(t)
        buckets[cause].append(dict(t))
        bucket_cat[cause] = cat

    rows: list[dict[str, Any]] = []
    for cause in sorted(buckets.keys(), key=lambda c: sum(_num(t.get("pnl_yen_100")) for t in buckets[c])):
        trades = buckets[cause]
        losses = [_num(t.get("pnl_yen_100")) for t in trades]
        reasons = Counter(_resolved_exit_reason(t) for t in trades)
        rows.append(
            {
                "loss_cause": cause,
                "improvement_bucket": bucket_cat.get(cause, "OTHER"),
                "trade_count": len(trades),
                "total_loss_yen": round(sum(losses), 2),
                "contribution_pct": round(sum(losses) / total_loss * 100.0, 2),
                "avg_loss_yen": round(statistics.mean(losses), 2),
                "avg_mfe_pct": round(statistics.mean(_mfe_pct(t) for t in trades), 4),
                "exit_reason_top": reasons.most_common(1)[0][0] if reasons else "",
            }
        )
    return rows


def _build_opportunity_loss_rows(
    *,
    accepted: Sequence[Mapping[str, Any]],
    reject_rows: Sequence[Mapping[str, Any]],
    capital_1m: Mapping[str, Any],
    unlimited_pnl: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    exit_opp = sum(_exit_opportunity_yen(t) for t in accepted)
    rows.append(
        {
            "category": "EXIT",
            "subcategory": "mfe_opportunity_loss",
            "opportunity_yen": round(exit_opp, 2),
            "opportunity_yen_signed": round(exit_opp, 2),
            "trade_count": len(accepted),
            "method": "sum(entry_price * max(0,mfe-realized)/100 * 100sh)",
            "notes": "Theoretical upside left on table at current exits",
        }
    )

    trailing_losses = sum(
        _num(t.get("pnl_yen_100"))
        for t in accepted
        if _classify_loss(t)[0] == "trailing_giveback"
    )
    rows.append(
        {
            "category": "EXIT",
            "subcategory": "trailing_giveback_realized_loss",
            "opportunity_yen": round(abs(trailing_losses), 2),
            "opportunity_yen_signed": round(-abs(trailing_losses), 2),
            "trade_count": sum(1 for t in accepted if _classify_loss(t)[0] == "trailing_giveback"),
            "method": "realized loss on trailing giveback trades",
            "notes": "Phase561 T2/T3 did not pass full-period validation",
        }
    )

    entry_rejects = [r for r in reject_rows if _reject_category(_primary_reject_reason(r)) == "ENTRY"]
    entry_pnls = [_num(r.get("pnl_yen_100")) for r in entry_rejects]
    entry_winners = sum(p for p in entry_pnls if p > 0)
    entry_losers_prevented = sum(-p for p in entry_pnls if p < 0)
    rows.append(
        {
            "category": "ENTRY",
            "subcategory": "guard_blocked_net_counterfactual",
            "opportunity_yen": round(abs(entry_winners), 2),
            "opportunity_yen_signed": round(sum(entry_pnls), 2),
            "trade_count": len(entry_rejects),
            "method": "counterfactual pnl of entry-guard rejects",
            "notes": f"blocked_winners={round(entry_winners,2)} prevented_loss={round(entry_losers_prevented,2)}",
        }
    )

    slm = [r for r in entry_rejects if "stop_low_mfe" in _primary_reject_reason(r)]
    slm_pnls = [_num(r.get("pnl_yen_100")) for r in slm]
    rows.append(
        {
            "category": "ENTRY",
            "subcategory": "stop_low_mfe_guard",
            "opportunity_yen": round(sum(p for p in slm_pnls if p > 0), 2),
            "opportunity_yen_signed": round(sum(slm_pnls), 2),
            "trade_count": len(slm),
            "method": "SLM guard blocked counterfactual",
            "notes": "Phase558 SLM net positive on blocked cohort in live",
        }
    )

    cluster = [r for r in entry_rejects if "entry_cluster" in _primary_reject_reason(r)]
    cluster_pnls = [_num(r.get("pnl_yen_100")) for r in cluster]
    rows.append(
        {
            "category": "ENTRY",
            "subcategory": "entry_cluster_guard",
            "opportunity_yen": round(sum(p for p in cluster_pnls if p > 0), 2),
            "opportunity_yen_signed": round(sum(cluster_pnls), 2),
            "trade_count": len(cluster),
            "method": "cluster guard blocked counterfactual",
            "notes": "",
        }
    )

    cap_rejects = [r for r in reject_rows if _reject_category(_primary_reject_reason(r)) == "CAP"]
    cap_pnls = [_num(r.get("pnl_yen_100")) for r in cap_rejects]
    rows.append(
        {
            "category": "CAP",
            "subcategory": "cap_collision_rejects",
            "opportunity_yen": round(abs(sum(cap_pnls)), 2),
            "opportunity_yen_signed": round(sum(cap_pnls), 2),
            "trade_count": len(cap_rejects),
            "method": "counterfactual pnl of cap-sim rejects",
            "notes": "CAP_SPLIT_4_1 cap extension period",
        }
    )

    cap_loss_accepted = sum(
        _num(t.get("pnl_yen_100"))
        for t in accepted
        if _classify_loss(t)[0] == "overlap_replaced"
    )
    rows.append(
        {
            "category": "CAP",
            "subcategory": "overlap_replaced_losses",
            "opportunity_yen": round(abs(cap_loss_accepted), 2),
            "opportunity_yen_signed": round(cap_loss_accepted, 2),
            "trade_count": sum(1 for t in accepted if _classify_loss(t)[0] == "overlap_replaced"),
            "method": "realized loss on overlap replaced exits",
            "notes": "",
        }
    )

    cap_skip_pnl = round(unlimited_pnl - _num(capital_1m.get("total_pnl_yen")), 2)
    rows.append(
        {
            "category": "Capital",
            "subcategory": "1M_equity_skip_opportunity",
            "opportunity_yen": round(cap_skip_pnl, 2),
            "opportunity_yen_signed": round(cap_skip_pnl, 2),
            "trade_count": int(capital_1m.get("capital_skip_count") or 0),
            "method": "unlimited_pnl - 1M fixed_100 sequential sim",
            "notes": "Phase567: not fixable by Runtime; needs capital",
        }
    )

    rows.append(
        {
            "category": "Position sizing",
            "subcategory": "policy_vs_fixed_100",
            "opportunity_yen": 0.0,
            "opportunity_yen_signed": 0.0,
            "trade_count": 0,
            "method": "Phase566 no runtime candidate",
            "notes": "fixed_100 optimal; sizing change not recommended",
        }
    )

    rows.append(
        {
            "category": "Universe",
            "subcategory": "dynamic40_exclusion",
            "opportunity_yen": 0.0,
            "opportunity_yen_signed": 0.0,
            "trade_count": 0,
            "method": "not quantified in accepted/reject cohort",
            "notes": "requires separate universe shadow; no yen estimate this phase",
        }
    )

    return rows


def _build_improvement_ranking(opportunity_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in opportunity_rows:
        signed = _num(row.get("opportunity_yen_signed"))
        cat = str(row.get("category") or "")
        sub = str(row.get("subcategory") or "")
        runtime = cat in ("EXIT", "ENTRY", "CAP") and abs(signed) >= 10000 and cat != "Capital"
        direction = "improve" if signed > 0 else "reduce_loss" if signed < 0 else "hold"
        ranked.append(
            {
                "category": cat,
                "subcategory": sub,
                "opportunity_yen": abs(_num(row.get("opportunity_yen"))),
                "opportunity_yen_signed": round(signed, 2),
                "direction": direction,
                "trade_count": row.get("trade_count"),
                "notes": row.get("notes"),
                "runtime_change_candidate": runtime and signed > 0,
            }
        )
    ranked.sort(key=lambda r: -abs(_num(r.get("opportunity_yen_signed"))))
    out: list[dict[str, Any]] = []
    for i, row in enumerate(ranked, start=1):
        out.append({"rank": i, **row})
    return out


def _mandatory_answers(
    *,
    unlimited_pnl: float,
    summary_by_cap: Mapping[int, Mapping[str, Any]],
    opportunity_rows: Sequence[Mapping[str, Any]],
    ranking: Sequence[Mapping[str, Any]],
    reject_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_cat: dict[str, float] = defaultdict(float)
    for row in opportunity_rows:
        cat = str(row.get("category") or "")
        by_cat[cat] += _num(row.get("opportunity_yen_signed"))

    top = ranking[0] if ranking else {}
    runtime_candidates = [r for r in ranking if r.get("runtime_change_candidate")]

    cap_1m = summary_by_cap.get(1_000_000, {})
    recovery = round(_num(cap_1m.get("total_pnl_yen")) / unlimited_pnl * 100.0, 2) if unlimited_pnl else 0.0

    return {
        "1_largest_improvement_headroom": {
            "category": top.get("category"),
            "subcategory": top.get("subcategory"),
            "opportunity_yen_signed": top.get("opportunity_yen_signed"),
        },
        "2_entry_improvement_yen": round(by_cat.get("ENTRY", 0.0), 2),
        "3_exit_improvement_yen": round(by_cat.get("EXIT", 0.0), 2),
        "4_capital_improvement_yen": round(by_cat.get("Capital", 0.0), 2),
        "5_universe_improvement_yen": round(by_cat.get("Universe", 0.0), 2),
        "6_cap_improvement_yen": round(by_cat.get("CAP", 0.0), 2),
        "7_position_sizing_improvement_yen": round(by_cat.get("Position sizing", 0.0), 2),
        "8_runtime_change_needed": len(runtime_candidates) > 0,
        "8_runtime_change_candidates": [
            f"{r.get('category')}/{r.get('subcategory')}" for r in runtime_candidates[:5]
        ],
        "9_largest_bottleneck": top.get("subcategory"),
        "10_recommended_operating_capital_yen": 2_500_000,
        "10_profit_recovery_1M_pct": recovery,
        "11_next_phase": "phase570_runtime_improvement_prioritization",
        "reference_unlimited_pnl_yen": unlimited_pnl,
        "reference_accepted_pnl_yen": unlimited_pnl,
        "reject_opportunity_total_yen": round(
            sum(_num(r.get("counterfactual_pnl_yen")) for r in reject_rows), 2
        ),
    }


@dataclass
class Phase569Job:
    repo_root: Path
    period_start: str = FULL_START
    live_start: str = PERIOD_START_LIVE
    period_end: str = "20991231"

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        end = min(self.period_end, _latest_live_day(repo))
        cap_end = (datetime.strptime(self.live_start, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")

        accepted = _load_full_period_accepted(
            repo, full_start=self.period_start, live_start=self.live_start, end=end
        )
        if not accepted:
            raise RuntimeError("No Phase558 accepted trades for Phase569")

        accepted_keys = {
            (str(t.get("symbol") or ""), str(t.get("entry_time") or "")) for t in accepted
        }
        cap_keys = {
            (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            for t in accepted
            if str(t.get("day") or "")[:8] <= cap_end
        }

        entry_rejects = _load_entry_guard_rejects(repo, live_start=self.live_start, end=end)
        cap_rejects = _load_cap_rejects(
            repo,
            period_start=self.period_start,
            period_end=cap_end,
            accepted_keys=cap_keys,
        )
        all_rejects = entry_rejects + cap_rejects

        trades = _prepare_trades(accepted)
        unlimited = _unlimited_pnl(trades)
        sim_1m = simulate_sizing_policy(trades, initial_equity=1_000_000, policy="fixed_100")

        reject_opportunity = _build_reject_opportunity_rows(all_rejects)
        loss_attribution = _build_loss_attribution(accepted)
        opportunity_loss = _build_opportunity_loss_rows(
            accepted=accepted,
            reject_rows=all_rejects,
            capital_1m=sim_1m,
            unlimited_pnl=unlimited,
        )
        ranking = _build_improvement_ranking(opportunity_loss)

        mandatory = _mandatory_answers(
            unlimited_pnl=unlimited,
            summary_by_cap={1_000_000: sim_1m},
            opportunity_rows=opportunity_loss,
            ranking=ranking,
            reject_rows=reject_opportunity,
        )

        return {
            "verdict": PHASE569_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.period_start}-{end}",
            "accepted_count": len(accepted),
            "rejected_count": len(all_rejects),
            "unlimited_pnl_yen": unlimited,
            "accepted_pnl_yen_100": unlimited,
            "improvement_ranking": ranking,
            "reject_opportunity": reject_opportunity,
            "loss_attribution": loss_attribution,
            "opportunity_loss": opportunity_loss,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root.resolve())
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "ranking": reports / "phase569_runtime_improvement_ranking.csv",
            "reject": reports / "phase569_reject_opportunity.csv",
            "loss": reports / "phase569_loss_attribution.csv",
            "opportunity": reports / "phase569_opportunity_loss_ranking.csv",
            "report": reports / "phase569_report.json",
            "doc": resolve_kabu_root(self.repo_root)
            / "docs"
            / "operations"
            / "phase569_runtime_improvement_opportunity.md",
        }
        _write_csv(paths["ranking"], IMPROVEMENT_RANKING_FIELDS, list(result.get("improvement_ranking") or []))
        _write_csv(paths["reject"], REJECT_OPPORTUNITY_FIELDS, list(result.get("reject_opportunity") or []))
        _write_csv(paths["loss"], LOSS_ATTRIBUTION_FIELDS, list(result.get("loss_attribution") or []))
        _write_csv(paths["opportunity"], OPPORTUNITY_LOSS_FIELDS, list(result.get("opportunity_loss") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        ma = result.get("mandatory_answers") or {}
        paths["doc"].parent.mkdir(parents=True, exist_ok=True)
        paths["doc"].write_text(
            "\n".join(
                [
                    "# Phase569 — Runtime Improvement Opportunity Mining",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {result.get('period')}",
                    f"**Accepted:** {result.get('accepted_count')} | **Rejected:** {result.get('rejected_count')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. largest headroom: {ma.get('1_largest_improvement_headroom')}",
                    f"2. ENTRY yen: {ma.get('2_entry_improvement_yen')}",
                    f"3. EXIT yen: {ma.get('3_exit_improvement_yen')}",
                    f"4. Capital yen: {ma.get('4_capital_improvement_yen')}",
                    f"5. Universe yen: {ma.get('5_universe_improvement_yen')}",
                    f"6. CAP yen: {ma.get('6_cap_improvement_yen')}",
                    f"7. Position sizing yen: {ma.get('7_position_sizing_improvement_yen')}",
                    f"8. runtime change needed: {ma.get('8_runtime_change_needed')}",
                    f"9. largest bottleneck: {ma.get('9_largest_bottleneck')}",
                    f"10. recommended capital: {ma.get('10_recommended_operating_capital_yen')} yen",
                    f"11. next phase: {ma.get('11_next_phase')}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return paths
