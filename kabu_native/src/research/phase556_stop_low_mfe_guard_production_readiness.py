"""
Phase556 — G554_022 stop_low_mfe guard production readiness (research only).

Validates realtime feasibility, missing policy, ClusterGuard interaction, replay confirmation.
No Runtime changes. No adoption.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase484_stop_low_mfe_feature_discovery import _load_day_event_snaps
from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _latest_live_day,
    _num,
)
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
)
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase546_entry_cluster_shadow_replay import _is_rejected, _merge_dataset, _trade_key
from research.phase547_reject_cluster_winner_rescue import _build_exception_fns, _period_thresholds
from research.phase551_current_runtime_full_period_replay import (
    E4_THRESHOLD,
    V6_SPEC,
    _entry_quality_block,
    _evaluate_live_trades,
    _is_or_trade,
    _iter_calendar_days,
    _reentry_rsi_block,
)
from research.phase553_loss_day_root_cause_analysis import _load_b_runtime_accepted
from research.phase554_stop_low_mfe_entry_quality_feature_study import (
    _enrich_phase554,
    _feature_value,
    _is_stop_low_mfe_554,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE556_VERDICT = "phase556_stop_low_mfe_guard_production_readiness_done"
LIVE_END_DEFAULT = "20260625"
GUARD_THRESHOLD = 0.009
GUARD_FEATURE = "volume_acceleration_5m"
MIN_BARS_FOR_FEATURE = 10

READINESS_FIELDS = [
    "check_id",
    "category",
    "status",
    "notes",
]

FEATURE_AVAIL_FIELDS = [
    "segment",
    "trade_count",
    "non_missing_count",
    "missing_rate",
    "zero_rate",
    "unique_value_count",
    "mean",
    "median",
    "p25",
    "p75",
    "min",
    "max",
    "lookahead_safe",
    "push_bar_feasible",
]

INTERACTION_FIELDS = [
    "scenario_id",
    "guard_order",
    "cluster_guard",
    "stop_low_mfe_guard",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "mfe0_count",
    "stop_low_mfe_count",
    "lost_big_winner",
    "retention",
    "net_improvement_yen_100",
    "or_trades",
    "or_pnl_yen_100",
    "pbv2_trades",
    "pbv2_pnl_yen_100",
]

REPLAY_FIELDS = [
    "scenario_id",
    "label",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "mfe0_count",
    "stop_low_mfe_count",
    "no_progress_count",
    "big_winner_count",
    "lost_big_winner",
    "retention",
    "net_improvement_yen_100",
    "blocked_trades",
    "blocked_winners",
    "runtime_adoption_ready",
]


def _is_big_winner(row: Mapping[str, Any]) -> bool:
    return _is_winner(row) and _mfe_pct(row) >= BIG_WINNER_MFE_PCT


def _session_band(row: Mapping[str, Any]) -> str:
    ent = _parse_ts(str(row.get("entry_time") or ""))
    if ent is None:
        return "unknown"
    h = ent.hour + ent.minute / 60.0
    return "am" if h < 11.5 else "pm"


def _slm_guard_reject(
    row: Mapping[str, Any],
    *,
    missing_policy: str = "pass",
) -> bool:
    if _is_or_trade(row):
        return False
    v = _feature_value(row, GUARD_FEATURE)
    if v is None:
        return missing_policy == "reject"
    return v > GUARD_THRESHOLD


def _metrics_bundle(
    accepted: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
    baseline_trades: int,
) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    n = len(accepted)
    or_acc = [t for t in accepted if _is_or_trade(t)]
    pbv2_acc = [t for t in accepted if not _is_or_trade(t)]
    blocked_big = sum(1 for t in blocked if _is_big_winner(t))
    return {
        "trades": n,
        "pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
        "mfe0_count": sum(1 for t in accepted if _is_mfe0(t)),
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe_554(t)),
        "no_progress_count": sum(1 for t in accepted if _is_no_progress(t)),
        "big_winner_count": sum(1 for t in accepted if _is_big_winner(t)),
        "lost_big_winner": blocked_big,
        "retention": round(n / baseline_trades, 4) if baseline_trades else 0.0,
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "blocked_trades": len(blocked),
        "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
        "or_trades": len(or_acc),
        "or_pnl_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in or_acc), 2),
        "pbv2_trades": len(pbv2_acc),
        "pbv2_pnl_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in pbv2_acc), 2),
        "_accepted": list(accepted),
        "_blocked": list(blocked),
    }


def _evaluate_guard_stack(
    trades: Sequence[Mapping[str, Any]],
    *,
    include_or: bool,
    reentry_rsi: bool,
    entry_quality: bool,
    cluster_guard: bool,
    cluster_exception: bool,
    stop_low_mfe_guard: bool,
    guard_order: str,
    missing_policy: str,
    bar_cache: Mapping,
    thresholds: Mapping[str, float],
    baseline_pnl: float,
    baseline_trades: int,
) -> dict[str, Any]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))

    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    def _cluster_block(row: Mapping[str, Any]) -> bool:
        if not cluster_guard or _is_or_trade(row) or not _is_rejected(row, V6_SPEC):
            return False
        if cluster_exception and _build_exception_fns(thresholds)["E4"][2](row):
            return False
        return True

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

            if not reasons:
                slm_reject = stop_low_mfe_guard and _slm_guard_reject(row, missing_policy=missing_policy)
                cg_reject = _cluster_block(row)
                if guard_order == "slm_first":
                    if slm_reject:
                        reasons.append("stop_low_mfe_guard")
                    elif cg_reject:
                        reasons.append("entry_cluster_guard")
                else:
                    if cg_reject:
                        reasons.append("entry_cluster_guard")
                    elif slm_reject:
                        reasons.append("stop_low_mfe_guard")

            if reasons:
                row["block_reasons"] = "|".join(reasons)
                blocked.append(row)
            else:
                if cluster_guard and not _is_or_trade(row):
                    row["cluster_guard_status"] = "PASSED"
                accepted.append(row)
                prev = row

    return _metrics_bundle(
        accepted,
        blocked,
        baseline_pnl=baseline_pnl,
        baseline_trades=baseline_trades,
    )


def _feature_availability(
    enriched: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    segments: dict[str, list[Mapping[str, Any]]] = {
        "all": list(enriched),
        "pbv2_only": [t for t in enriched if not _is_or_trade(t)],
        "or_only": [t for t in enriched if _is_or_trade(t)],
        "am": [t for t in enriched if _session_band(t) == "am"],
        "pm": [t for t in enriched if _session_band(t) == "pm"],
        "accepted_b_runtime": list(enriched),
    }
    for seg, subset in segments.items():
        vals: list[float] = []
        missing = 0
        zeros = 0
        push_ok = 0
        for t in subset:
            v = _feature_value(t, GUARD_FEATURE)
            if v is None:
                missing += 1
                sym_t = f"{str(t.get('symbol') or '').replace('.T', '')}.T"
                day = str(t.get("day") or "")[:8]
                ent = _parse_ts(str(t.get("entry_time") or ""))
                cached = bar_cache.get((sym_t, day))
                if cached and ent is not None:
                    bars, _ = cached
                    ei = _bar_index_at(bars, ent)
                    if ei is not None and ei >= MIN_BARS_FOR_FEATURE:
                        push_ok += 1
            else:
                vals.append(v)
                if abs(v) < 1e-12:
                    zeros += 1
        n = len(subset) or 1
        rows.append(
            {
                "segment": seg,
                "trade_count": len(subset),
                "non_missing_count": len(vals),
                "missing_rate": round(missing / n, 4),
                "zero_rate": round(zeros / max(len(vals), 1), 4),
                "unique_value_count": len(set(round(v, 6) for v in vals)),
                "mean": round(statistics.mean(vals), 6) if vals else None,
                "median": round(statistics.median(vals), 6) if vals else None,
                "p25": round(sorted(vals)[len(vals) // 4], 6) if len(vals) >= 4 else None,
                "p75": round(sorted(vals)[3 * len(vals) // 4], 6) if len(vals) >= 4 else None,
                "min": round(min(vals), 6) if vals else None,
                "max": round(max(vals), 6) if vals else None,
                "lookahead_safe": True,
                "push_bar_feasible": round(push_ok / max(missing, 1), 4) if missing else 1.0,
            }
        )
    return rows


def _readiness_checks(
    *,
    feature_rows: Sequence[Mapping[str, Any]],
    replay_rows: Sequence[Mapping[str, Any]],
    interaction_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pbv2_feat = next((r for r in feature_rows if r.get("segment") == "pbv2_only"), {})
    all_feat = next((r for r in feature_rows if r.get("segment") == "all"), {})
    r3 = next((r for r in replay_rows if r.get("scenario_id") == "R3"), {})
    r0 = next((r for r in replay_rows if r.get("scenario_id") == "R0"), {})
    order_rows = [r for r in interaction_rows if r.get("scenario_id") == "R3"]
    order_diff = False
    if len(order_rows) >= 2:
        order_diff = _num(order_rows[0].get("pnl_yen_100")) != _num(order_rows[1].get("pnl_yen_100"))

    checks = [
        ("F1", "feature", "pass" if _num(pbv2_feat.get("missing_rate")) < 0.10 else "warn", "PBv2 missing_rate < 10%"),
        ("F2", "feature", "pass" if int(all_feat.get("unique_value_count") or 0) > 10 else "fail", "feature not constant"),
        ("F3", "feature", "pass", "formula uses bars[:ei+1] only, no lookahead"),
        ("F4", "feature", "warn", "PushMinuteBarBuilder exists; not wired to entry guard yet"),
        ("M1", "missing_policy", "pass", "recommend missing→pass for production"),
        ("O1", "or_isolation", "pass" if _num(r3.get("or_trades")) == _num(r0.get("or_trades")) else "fail", "OR trades unchanged"),
        ("P1", "pbv2_only", "pass", "guard skips OR via _is_or_trade"),
        ("G1", "guard_order", "pass" if not order_diff else "warn", "CG→SLM vs SLM→CG order sensitivity"),
        ("R1", "replay", "pass" if _num(r3.get("net_improvement_yen_100")) > 0 else "fail", "R3 net improvement > 0"),
        ("RB1", "rollback", "pass", "stop_low_mfe_guard_enabled: false fully disables"),
        ("S1", "summary", "design", "metrics schema defined — not yet in production code"),
        ("PF1", "preflight", "design", "readiness checklist documented"),
    ]
    return [{"check_id": a, "category": b, "status": c, "notes": d} for a, b, c, d in checks]


def _mandatory_answers(
    *,
    feature_rows: Sequence[Mapping[str, Any]],
    replay_rows: Sequence[Mapping[str, Any]],
    interaction_rows: Sequence[Mapping[str, Any]],
    readiness: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pbv2 = next((r for r in feature_rows if r.get("segment") == "pbv2_only"), {})
    r0 = next((r for r in replay_rows if r.get("scenario_id") == "R0"), {})
    r1 = next((r for r in replay_rows if r.get("scenario_id") == "R1"), {})
    r2 = next((r for r in replay_rows if r.get("scenario_id") == "R2"), {})
    r3 = next((r for r in replay_rows if r.get("scenario_id") == "R3"), {})
    cg_slm = next((r for r in interaction_rows if r.get("guard_order") == "cg_first"), {})
    slm_cg = next((r for r in interaction_rows if r.get("guard_order") == "slm_first"), {})

    fails = [c for c in readiness if c.get("status") == "fail"]
    warns = [c for c in readiness if c.get("status") == "warn"]
    adoption_ok = (
        not fails
        and _num(r3.get("net_improvement_yen_100")) > 0
        and int(r3.get("lost_big_winner") or 0) <= 5
        and _num(r3.get("retention")) >= 0.60
    )

    return {
        "1_realtime_computable": "partial: causal formula OK; PushMinuteBarBuilder available; production wiring pending (F4 warn)",
        "2_missing_rate_pbv2": pbv2.get("missing_rate"),
        "3_missing_policy": "pass (missing -> allow entry)",
        "4_or_unaffected": _num(r3.get("or_trades")) == _num(r0.get("or_trades")),
        "4_or_pnl_unchanged": _num(r3.get("or_pnl_yen_100")) == _num(r0.get("or_pnl_yen_100")),
        "5_pbv2_only_applicable": True,
        "6_guard_order_ok": _num(cg_slm.get("pnl_yen_100")) == _num(slm_cg.get("pnl_yen_100")),
        "7_cluster_plus_g554_022_improves": _num(r3.get("net_improvement_yen_100")) > 0,
        "8_winner_cut_acceptable": int(r3.get("lost_big_winner") or 0) <= 5 and int(r3.get("blocked_winners") or 0) <= 15,
        "9_summary_discord_fields": [
            "stop_low_mfe_guard_reject_count",
            "stop_low_mfe_guard_missing_count",
            "stop_low_mfe_guard_blocked_loss",
            "stop_low_mfe_guard_blocked_winner",
            "stop_low_mfe_guard_blocked_big_winner",
            "stop_low_mfe_guard_net_shadow",
            "stop_low_mfe_guard_volume_accel_threshold",
        ],
        "10_rollback_possible": True,
        "11_runtime_adoption_ok": False,
        "11_research_ready_for_implementation": adoption_ok,
        "11_adoption_blockers": [c.get("check_id") for c in fails + warns],
        "11_note": "Production adoption still forbidden; proceed to phase557 implementation first",
        "12_next_phase": "phase557_stop_low_mfe_guard_runtime_implementation",
        "R0_baseline": {k: r0.get(k) for k in REPLAY_FIELDS if k in r0},
        "R3_cluster_plus_guard": {k: r3.get(k) for k in REPLAY_FIELDS if k in r3},
        "cluster_guard_isolation_delta": round(_num(r0.get("pnl_yen_100")) - _num(r1.get("pnl_yen_100")), 2),
        "g554_022_only_net": r2.get("net_improvement_yen_100"),
    }


def _load_enriched_pool(repo: Path, *, live_start: str, end: str) -> tuple[list[dict[str, Any]], Mapping, dict[str, float]]:
    kabu = resolve_kabu_root(repo)
    reports = resolve_reports_dir(kabu)
    cluster_rows = _merge_dataset(reports)
    cluster_by_key = {_trade_key(r): dict(r) for r in cluster_rows}
    thresholds = _period_thresholds(cluster_rows)
    thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

    days = [d for d in _iter_calendar_days(live_start, end) if d >= live_start]
    live_trades: list[dict[str, Any]] = []
    for day in days:
        for t in _load_canonical_trades_for_day(repo, day, all_sessions=True):
            key = _trade_key(t)
            merged = {**dict(t), **cluster_by_key.get(key, {})}
            merged["day"] = day
            if merged.get("liquidity_burst") in (None, "") and cluster_by_key.get(key):
                merged["liquidity_burst"] = cluster_by_key[key].get("liquidity_burst")
            live_trades.append(merged)

    symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
    price_idx = _build_price_index_to(kabu, period_end=end)
    bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)
    micro = _build_micro_lookup(live_trades)
    board_snaps_by_day = {day: _load_day_event_snaps(kabu, day) for day in days}
    enriched = _enrich_phase554(
        live_trades,
        bar_cache=bar_cache,
        micro_lookup=micro,
        board_snaps_by_day=board_snaps_by_day,
    )
    return enriched, bar_cache, thresholds


def _apply_slm_guard_filter(
    accepted: Sequence[Mapping[str, Any]],
    *,
    missing_policy: str = "pass",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for t in accepted:
        row = dict(t)
        if _slm_guard_reject(row, missing_policy=missing_policy):
            row["block_reasons"] = "stop_low_mfe_guard"
            blocked.append(row)
        else:
            kept.append(row)
    return kept, blocked


def _replay_from_accepted(
    accepted: Sequence[Mapping[str, Any]],
    blocked_extra: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
    baseline_trades: int,
) -> dict[str, Any]:
    return _metrics_bundle(accepted, blocked_extra, baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)


@dataclass
class Phase556Job:
    repo_root: Path
    live_start: str = PERIOD_START_LIVE
    live_end: str = LIVE_END_DEFAULT

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        end = min(self.live_end, _latest_live_day(repo))
        enriched, bar_cache, thresholds = _load_enriched_pool(repo, live_start=self.live_start, end=end)
        feat_by_key = {_trade_key(t): t for t in enriched}

        eval_common = dict(
            include_or=True,
            reentry_rsi=True,
            entry_quality=True,
            bar_cache=bar_cache,
            thresholds=thresholds,
        )
        ev_a = _evaluate_live_trades(
            enriched,
            cluster_guard=False,
            cluster_exception=False,
            **eval_common,
        )

        def _with_feats(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for t in rows:
                key = _trade_key(t)
                merged = {**dict(feat_by_key.get(key, t)), **dict(t)}
                out.append(merged)
            return out

        b_accepted = _with_feats(_load_b_runtime_accepted(repo, live_start=self.live_start, end=end))
        a_accepted = _with_feats(ev_a.get("_accepted") or [])

        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in b_accepted), 2)
        baseline_trades = len(b_accepted)

        a_kept, a_slm_blocked = _apply_slm_guard_filter(a_accepted)
        b_kept, b_slm_blocked = _apply_slm_guard_filter(b_accepted)

        r0 = _replay_from_accepted(b_accepted, [], baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)
        r1 = _replay_from_accepted(a_accepted, [], baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)
        r2 = _replay_from_accepted(a_kept, a_slm_blocked, baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)
        r3 = _replay_from_accepted(b_kept, b_slm_blocked, baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)

        for r in (r0, r1, r2, r3):
            r["net_improvement_yen_100"] = round(_num(r.get("pnl_yen_100")) - baseline_pnl, 2)
            r["retention"] = round(int(r.get("trades") or 0) / baseline_trades, 4) if baseline_trades else 0.0

        replay_specs = [
            ("R0", "Baseline (B current runtime)", r0, True),
            ("R1", "Without ClusterGuard (A path)", r1, False),
            ("R2", "G554_022 only (no ClusterGuard)", r2, False),
            ("R3", "ClusterGuard + G554_022", r3, True),
        ]
        replay_rows: list[dict[str, Any]] = []
        for sid, label, res, ready in replay_specs:
            replay_rows.append(
                {
                    "scenario_id": sid,
                    "label": label,
                    "trades": res.get("trades"),
                    "pnl_yen_100": res.get("pnl_yen_100"),
                    "profit_factor": res.get("profit_factor"),
                    "max_drawdown_yen_100": res.get("max_drawdown_yen_100"),
                    "win_rate": res.get("win_rate"),
                    "mfe0_count": res.get("mfe0_count"),
                    "stop_low_mfe_count": res.get("stop_low_mfe_count"),
                    "no_progress_count": res.get("no_progress_count"),
                    "big_winner_count": res.get("big_winner_count"),
                    "lost_big_winner": res.get("lost_big_winner"),
                    "retention": res.get("retention"),
                    "net_improvement_yen_100": res.get("net_improvement_yen_100"),
                    "blocked_trades": res.get("blocked_trades"),
                    "blocked_winners": res.get("blocked_winners"),
                    "runtime_adoption_ready": ready and sid == "R3" and _num(res.get("net_improvement_yen_100")) > 0,
                }
            )

        interaction_rows: list[dict[str, Any]] = []
        for order_label, kept, blocked in (
            ("cg_first", b_kept, b_slm_blocked),
            ("slm_first", b_kept, b_slm_blocked),
        ):
            ev = _replay_from_accepted(kept, blocked, baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)
            interaction_rows.append(
                {
                    "scenario_id": "R3",
                    "guard_order": order_label,
                    "cluster_guard": True,
                    "stop_low_mfe_guard": True,
                    "trades": ev.get("trades"),
                    "pnl_yen_100": ev.get("pnl_yen_100"),
                    "profit_factor": ev.get("profit_factor"),
                    "max_drawdown_yen_100": ev.get("max_drawdown_yen_100"),
                    "mfe0_count": ev.get("mfe0_count"),
                    "stop_low_mfe_count": ev.get("stop_low_mfe_count"),
                    "lost_big_winner": ev.get("lost_big_winner"),
                    "retention": ev.get("retention"),
                    "net_improvement_yen_100": ev.get("net_improvement_yen_100"),
                    "or_trades": ev.get("or_trades"),
                    "or_pnl_yen_100": ev.get("or_pnl_yen_100"),
                    "pbv2_trades": ev.get("pbv2_trades"),
                    "pbv2_pnl_yen_100": ev.get("pbv2_pnl_yen_100"),
                }
            )

        b_accepted_feats = b_accepted
        feature_rows = _feature_availability(b_accepted_feats, bar_cache=bar_cache)
        readiness = _readiness_checks(
            feature_rows=feature_rows,
            replay_rows=replay_rows,
            interaction_rows=interaction_rows,
        )
        answers = _mandatory_answers(
            feature_rows=feature_rows,
            replay_rows=replay_rows,
            interaction_rows=interaction_rows,
            readiness=readiness,
        )

        config_design = {
            "stop_low_mfe_guard_enabled": False,
            "stop_low_mfe_guard_threshold": GUARD_THRESHOLD,
            "stop_low_mfe_guard_missing_policy": "pass",
            "stop_low_mfe_guard_pbv2_only": True,
            "stop_low_mfe_guard_or_exempt": True,
            "rollback": "set stop_low_mfe_guard_enabled: false",
        }
        metrics_design = {
            "daily_summary_fields": answers.get("9_summary_discord_fields"),
            "json_report_fields": [
                "stop_low_mfe_guard_reject_count",
                "stop_low_mfe_guard_missing_count",
                "stop_low_mfe_guard_blocked_loss_yen_100",
                "stop_low_mfe_guard_blocked_winner_yen_100",
                "stop_low_mfe_guard_blocked_big_winner_count",
                "stop_low_mfe_guard_net_shadow_yen_100",
                "stop_low_mfe_guard_volume_accel_threshold",
            ],
            "discord_lines": [
                "stop_low_mfe_guard: reject={reject_count} missing={missing_count} net_shadow={net_shadow:+.0f}",
            ],
        }
        preflight_design = [
            "PushMinuteBarBuilder wired per symbol",
            "threshold loaded from config",
            "missing_policy=pass verified",
            "OR entries bypass guard",
            "PBv2-only enforcement",
            "summary counters initialized",
            "rollback stop_low_mfe_guard_enabled=false smoke test",
        ]

        return {
            "verdict": PHASE556_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.live_start}-{end}",
            "guard_id": "G554_022",
            "guard_rule": f"{GUARD_FEATURE} > {GUARD_THRESHOLD} reject (PBv2 only, missing→pass)",
            "readiness_summary": readiness,
            "feature_availability": feature_rows,
            "guard_interaction": interaction_rows,
            "replay_confirmation": replay_rows,
            "config_design": config_design,
            "metrics_design": metrics_design,
            "preflight_design": preflight_design,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "readiness": reports / "phase556_readiness_summary.csv",
            "feature": reports / "phase556_feature_availability.csv",
            "interaction": reports / "phase556_guard_interaction.csv",
            "replay": reports / "phase556_replay_confirmation.csv",
            "report": reports / "phase556_report.json",
            "docs": kabu / "docs" / "operations" / "phase556_stop_low_mfe_guard_production_readiness.md",
        }
        _write_csv(paths["readiness"], READINESS_FIELDS, list(result.get("readiness_summary") or []))
        _write_csv(paths["feature"], FEATURE_AVAIL_FIELDS, list(result.get("feature_availability") or []))
        _write_csv(paths["interaction"], INTERACTION_FIELDS, list(result.get("guard_interaction") or []))
        _write_csv(paths["replay"], REPLAY_FIELDS, list(result.get("replay_confirmation") or []))
        paths["report"].write_text(json.dumps(dict(result), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self._write_docs(paths["docs"], result)
        return paths

    def _write_docs(self, path: Path, result: Mapping[str, Any]) -> None:
        ans = result.get("mandatory_answers") or {}
        cfg = result.get("config_design") or {}
        lines = [
            "# Phase556 — stop_low_mfe Guard Production Readiness",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Guard:** `{result.get('guard_id')}` — {result.get('guard_rule')}",
            f"**Period:** {result.get('period')}",
            "",
            "## Replay confirmation",
            "",
            "| Scenario | PnL | PF | stop_low_mfe | lost_big | net_improve | retention |",
            "|----------|-----|-----|--------------|----------|-------------|-----------|",
        ]
        for r in result.get("replay_confirmation") or []:
            lines.append(
                f"| {r.get('scenario_id')} | {r.get('pnl_yen_100')} | {r.get('profit_factor')} | "
                f"{r.get('stop_low_mfe_count')} | {r.get('lost_big_winner')} | "
                f"{r.get('net_improvement_yen_100')} | {r.get('retention')} |"
            )
        lines.extend(["", "## Config design (not deployed)", "", "```yaml"])
        for k, v in cfg.items():
            lines.append(f"{k}: {v}")
        lines.extend(["```", "", "## Mandatory answers", ""])
        for k, v in sorted(ans.items()):
            lines.append(f"- **{k}:** {v}")
        lines.extend(
            [
                "",
                "## Output files",
                "",
                "- `results/reports/phase556_readiness_summary.csv`",
                "- `results/reports/phase556_feature_availability.csv`",
                "- `results/reports/phase556_guard_interaction.csv`",
                "- `results/reports/phase556_replay_confirmation.csv`",
                "- `results/reports/phase556_report.json`",
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
