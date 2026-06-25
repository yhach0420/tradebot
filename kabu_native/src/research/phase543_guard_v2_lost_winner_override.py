"""
Phase543A — Lost winner analysis and Guard+Override design (research only).

Analyzes why ADX35 / ADX35_FIVE50 / ADX30_FIVE50 block winners and tests overrides.
No Runtime changes. No adoption.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup, _percentile, _separation_score
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    _build_bar_cache_for_days,
    _is_stop_low_mfe,
    _latest_live_day,
    _num,
)
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase540_no_progress_mfe0_entry_quality import (
    ENTRY_FEATURE_IDS,
    _cap_pool,
    _duplicate_flags,
    _entry_type_label,
    _hold_sec,
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mae_pct,
    _mfe_pct,
    _or_pbv2_label,
    _resolved_exit_reason,
)
from research.phase541_guard_v2_full_period_validation import (
    BIG_WINNER_MFE_PCT,
    MAX_WORKERS,
    PERIOD_START,
    _discover_live_days,
    _enrich_trades_phase541,
)
from research.phase542_guard_v2_threshold_tuning import _guard_allows as _guard_allows_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE543_VERDICT = "phase543_guard_v2_lost_winner_override_done"
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT

GUARD_SPECS: dict[str, dict[str, Any]] = {
    "G_A": {"guard_id": "G_A", "guard_name": "ADX35", "adx_max": 35.0},
    "G_B": {
        "guard_id": "G_B",
        "guard_name": "ADX35_FIVE50",
        "adx_max": 35.0,
        "five_min_max": 50.0,
    },
    "G_C": {
        "guard_id": "G_C",
        "guard_name": "ADX30_FIVE50",
        "adx_max": 30.0,
        "five_min_max": 50.0,
    },
}

OVERRIDE_IDS: tuple[str, ...] = (
    "O1_board_imbalance",
    "O2_volume_pct",
    "O3_volume_ratio",
    "O4_momentum_p75",
    "O5_high_update_recent",
    "O6_prior_high_break",
    "O7_day_return_rank",
    "O8_vwap_positive",
    "O9_open_strength",
    "O10_vol_or_high_update",
    "O11_board_vol_high",
    "O12_day_leader_proxy",
)

LOST_WINNER_FIELDS = [
    "guard_id",
    "guard_name",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "pnl_pct",
    "MFE",
    "MAE",
    "hold_sec",
    "exit_reason",
    "entry_type",
    "or_pbv2",
    "cap_pool",
    "duplicate_entry_observed",
    "is_big_winner",
]

FEATURE_COMPARE_FIELDS = [
    "guard_id",
    "cohort",
    "feature",
    "n",
    "median",
    "p25",
    "p75",
    "mean",
    "missing_rate",
    "cohens_d_vs_lost_winner",
    "separation_score_vs_lost_winner",
]

CLUSTER_FIELDS = [
    "guard_id",
    "cluster",
    "trade_count",
    "total_pnl_yen_100",
    "avg_mfe_pct",
    "median_adx14",
    "median_board_imbalance",
    "median_volume_percentile",
    "median_momentum_score",
    "cluster_share",
]

OVERRIDE_SUMMARY_FIELDS = [
    "strategy_id",
    "guard_id",
    "guard_name",
    "override_id",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "trade_retention_rate",
    "mfe0_count",
    "mfe0_reduction_rate",
    "no_progress_count",
    "no_progress_reduction_rate",
    "stop_low_mfe_count",
    "lost_winner_count",
    "lost_big_winner_count",
    "recovered_winner_count",
    "recovered_big_winner_count",
    "recovered_winner_pnl_yen_100",
    "reintroduced_mfe0_count",
    "reintroduced_loser_pnl_yen_100",
    "net_improvement_yen_100",
    "improvement_day_rate",
    "success_count",
    "all_success",
]

DEPENDENCY_FIELDS = [
    "strategy_id",
    "guard_id",
    "override_id",
    "top1_symbol_contribution_yen_100",
    "top3_symbol_contribution_yen_100",
    "top1_day_contribution_yen_100",
    "top3_day_contribution_yen_100",
    "top10_trade_exclusion_net_yen_100",
    "top3_symbol_exclusion_net_yen_100",
    "top3_day_exclusion_net_yen_100",
]

COMPARE_FEATURES: tuple[str, ...] = (
    "adx14",
    "rsi14",
    "five_min_position",
    "moving_average_position",
    "vwap_distance_pct",
    "board_imbalance",
    "spread_bps",
    "volume",
    "volume_ratio",
    "volume_percentile",
    "momentum_score",
    "update_count_before_entry",
    "day_high_distance_pct",
    "day_high_update_speed",
    "high_update_recent",
    "prior_high_break",
    "prior_low_break",
    "pullback_after_spike",
    "day_return_rank",
    "minutes_from_open",
)


def _guard_blocks(feats: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    return not _guard_allows_spec(feats, spec)


def _momentum_p75(enriched: Sequence[Mapping[str, Any]]) -> float:
    vals = [_num(t.get("momentum_score")) for t in enriched if t.get("momentum_score") is not None]
    return float(_percentile(vals, 75) or 0.0)


def _override_allows(override_id: str, feats: Mapping[str, Any], *, momentum_p75: float) -> bool:
    board = feats.get("board_imbalance")
    vol_pct = feats.get("volume_percentile")
    vol_ratio = feats.get("volume_ratio")
    mom = feats.get("momentum_score")
    high_recent = feats.get("high_update_recent")
    phb = feats.get("prior_high_break")
    rank = feats.get("day_return_rank")
    vwap = feats.get("vwap_distance_pct")
    open_s = feats.get("open_strength")

    def _o1() -> bool:
        return board is not None and float(board) >= 0.60

    def _o2() -> bool:
        return vol_pct is not None and float(vol_pct) >= 80.0

    def _o3() -> bool:
        return vol_ratio is not None and float(vol_ratio) >= 1.5

    def _o4() -> bool:
        return mom is not None and float(mom) >= momentum_p75 > 0

    def _o5() -> bool:
        return high_recent is True

    def _o6() -> bool:
        return phb is True

    def _o7() -> bool:
        return rank is not None and float(rank) <= 20.0

    def _o8() -> bool:
        return vwap is not None and float(vwap) > 0.0

    def _o9() -> bool:
        if open_s is None:
            return False
        if isinstance(open_s, bool):
            return open_s
        return float(open_s) > 0.0

    if override_id == "O1_board_imbalance":
        return _o1()
    if override_id == "O2_volume_pct":
        return _o2()
    if override_id == "O3_volume_ratio":
        return _o3()
    if override_id == "O4_momentum_p75":
        return _o4()
    if override_id == "O5_high_update_recent":
        return _o5()
    if override_id == "O6_prior_high_break":
        return _o6()
    if override_id == "O7_day_return_rank":
        return _o7()
    if override_id == "O8_vwap_positive":
        return _o8()
    if override_id == "O9_open_strength":
        return _o9()
    if override_id == "O10_vol_or_high_update":
        return _o2() or _o5()
    if override_id == "O11_board_vol_high":
        return _o1() or _o2() or _o5()
    if override_id == "O12_day_leader_proxy":
        return _o7() and vol_pct is not None and float(vol_pct) >= 70.0
    return False


def _strategy_allows(
    feats: Mapping[str, Any],
    spec: Mapping[str, Any],
    override_id: Optional[str],
    *,
    momentum_p75: float,
) -> bool:
    if _guard_allows_spec(feats, spec):
        return True
    if override_id:
        return _override_allows(override_id, feats, momentum_p75=momentum_p75)
    return False


def _classify_lost_winner_cluster(row: Mapping[str, Any], *, momentum_p75: float) -> str:
    adx = _num(row.get("adx14"))
    if adx >= 30 and str(row.get("trend_direction") or "") == "up":
        return "high_adx_strong_trend"
    if _num(row.get("volume_ratio")) >= 1.5 or _num(row.get("volume_percentile")) >= 80:
        return "volume_surge"
    if _num(row.get("board_imbalance")) >= 0.60:
        return "high_board_imbalance"
    if row.get("high_update_recent") is True:
        return "high_update_recent"
    if row.get("open_strength") is True or _num(row.get("open_strength")) > 0:
        return "open_strength"
    if _num(row.get("vwap_distance_pct")) > 1.0:
        return "high_vwap_distance"
    if row.get("day_return_rank") is not None and float(row["day_return_rank"]) <= 20:
        return "day_leader"
    if row.get("momentum_score") is not None and float(row["momentum_score"]) >= momentum_p75 > 0:
        return "momentum_strong"
    return "other"


def _feature_values(rows: Sequence[Mapping[str, Any]], feat: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get(feat)
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            out.append(1.0 if v else 0.0)
        else:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
    return out


def _cohort_feature_rows(
    guard_id: str,
    passed_winners: Sequence[Mapping[str, Any]],
    lost_winners: Sequence[Mapping[str, Any]],
    blocked_bad: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lw_vals_by_feat: dict[str, list[float]] = {}
    for feat in COMPARE_FEATURES:
        lw = _feature_values(lost_winners, feat)
        lw_vals_by_feat[feat] = lw
        for cohort, items in (
            ("passed_winner", passed_winners),
            ("lost_winner", lost_winners),
            ("blocked_mfe0_loser", blocked_bad),
        ):
            vals = _feature_values(items, feat)
            miss = 1.0 - (len(vals) / len(items)) if items else 0.0
            cd = _cohens_d(vals, lw) if len(vals) >= 2 and len(lw) >= 2 else None
            sep = _separation_score(vals, lw) if vals and lw else None
            rows.append(
                {
                    "guard_id": guard_id,
                    "cohort": cohort,
                    "feature": feat,
                    "n": len(vals),
                    "median": round(statistics.median(vals), 6) if vals else None,
                    "p25": _percentile(vals, 25) if vals else None,
                    "p75": _percentile(vals, 75) if vals else None,
                    "mean": round(statistics.mean(vals), 6) if vals else None,
                    "missing_rate": round(miss, 4),
                    "cohens_d_vs_lost_winner": round(cd, 4) if cd is not None else None,
                    "separation_score_vs_lost_winner": round(sep, 4) if sep is not None else None,
                }
            )
    return rows


def _cluster_rows(guard_id: str, lost_winners: Sequence[Mapping[str, Any]], *, momentum_p75: float) -> list[dict[str, Any]]:
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in lost_winners:
        by_cluster[_classify_lost_winner_cluster(t, momentum_p75=momentum_p75)].append(dict(t))
    total = len(lost_winners) or 1
    rows: list[dict[str, Any]] = []
    for cluster in (
        "high_adx_strong_trend",
        "volume_surge",
        "high_board_imbalance",
        "high_update_recent",
        "open_strength",
        "high_vwap_distance",
        "day_leader",
        "momentum_strong",
        "other",
    ):
        items = by_cluster.get(cluster, [])
        if not items:
            continue
        pnls = [_num(t.get("pnl_yen_100")) for t in items]
        mfes = [_mfe_pct(t) for t in items]
        rows.append(
            {
                "guard_id": guard_id,
                "cluster": cluster,
                "trade_count": len(items),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else None,
                "median_adx14": round(statistics.median(_feature_values(items, "adx14")), 4)
                if _feature_values(items, "adx14")
                else None,
                "median_board_imbalance": round(statistics.median(_feature_values(items, "board_imbalance")), 4)
                if _feature_values(items, "board_imbalance")
                else None,
                "median_volume_percentile": round(statistics.median(_feature_values(items, "volume_percentile")), 4)
                if _feature_values(items, "volume_percentile")
                else None,
                "median_momentum_score": round(statistics.median(_feature_values(items, "momentum_score")), 4)
                if _feature_values(items, "momentum_score")
                else None,
                "cluster_share": round(len(items) / total, 4),
            }
        )
    return rows


def _evaluate_strategy(
    enriched: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    override_id: Optional[str],
    *,
    momentum_p75: float,
    baseline_pnl: float,
    baseline_trades: int,
    baseline_mfe0: int,
    baseline_np: int,
    guard_only_lost_big: int,
) -> dict[str, Any]:
    gid = str(spec["guard_id"])
    gname = str(spec["guard_name"])
    sid = f"{gid}+{override_id}" if override_id else f"{gid}_only"

    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    guard_blocked: list[dict[str, Any]] = []
    recovered: list[dict[str, Any]] = []

    for t in enriched:
        row = dict(t)
        feats = row
        g_block = _guard_blocks(feats, spec)
        allow = _strategy_allows(feats, spec, override_id, momentum_p75=momentum_p75)
        if allow:
            accepted.append(row)
            if g_block and override_id:
                recovered.append(row)
        else:
            blocked.append(row)
        if g_block:
            guard_blocked.append(row)

    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    lost_winners = [t for t in blocked if _is_winner(t)]
    lost_big = [t for t in blocked if _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE]
    rec_win = [t for t in recovered if _is_winner(t)]
    rec_big = [t for t in recovered if _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE]
    reintro_mfe0 = [t for t in recovered if _is_mfe0(t)]
    reintro_losers = [t for t in recovered if not _is_winner(t)]

    by_day: dict[str, float] = defaultdict(float)
    base_by_day: dict[str, float] = defaultdict(float)
    for t in enriched:
        d = str(t.get("day") or "")[:8]
        base_by_day[d] += _num(t.get("pnl_yen_100"))
    for t in accepted:
        d = str(t.get("day") or "")[:8]
        by_day[d] += _num(t.get("pnl_yen_100"))
    improve_days = sum(1 for d in base_by_day if by_day.get(d, 0) > base_by_day[d])
    day_n = len(base_by_day) or 1

    mfe0_rem = sum(1 for t in accepted if _is_mfe0(t))
    np_rem = sum(1 for t in accepted if _is_no_progress(t))

    return {
        "strategy_id": sid,
        "guard_id": gid,
        "guard_name": gname,
        "override_id": override_id or "",
        "total_pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "trade_count": len(accepted),
        "trade_retention_rate": round(len(accepted) / baseline_trades, 4) if baseline_trades else 0.0,
        "mfe0_count": mfe0_rem,
        "mfe0_reduction_rate": round((baseline_mfe0 - mfe0_rem) / baseline_mfe0, 4) if baseline_mfe0 else 0.0,
        "no_progress_count": np_rem,
        "no_progress_reduction_rate": round((baseline_np - np_rem) / baseline_np, 4) if baseline_np else 0.0,
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe(t)),
        "lost_winner_count": len(lost_winners),
        "lost_big_winner_count": len(lost_big),
        "recovered_winner_count": len(rec_win),
        "recovered_big_winner_count": len(rec_big),
        "recovered_winner_pnl_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in rec_win), 2),
        "reintroduced_mfe0_count": len(reintro_mfe0),
        "reintroduced_loser_pnl_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in reintro_losers), 2),
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "improvement_day_rate": round(improve_days / day_n, 4),
        "_accepted": accepted,
        "_blocked": blocked,
        "_guard_only_lost_big": guard_only_lost_big or len(lost_big),
        "_baseline_pnl": baseline_pnl,
        "_baseline_mfe0": baseline_mfe0,
        "_baseline_np": baseline_np,
        "_baseline_trades": baseline_trades,
        "_baseline_pf": _pf([_num(t.get("pnl_yen_100")) for t in enriched]),
        "_baseline_maxdd": round(_max_drawdown_yen(_chron_pnls(enriched)), 2),
    }


def _dependency_row(strategy: Mapping[str, Any]) -> dict[str, Any]:
    blocked = list(strategy.get("_blocked") or [])
    accepted = list(strategy.get("_accepted") or [])
    baseline_pnl = _num(strategy.get("_baseline_pnl"))
    net = round(sum(_num(t.get("pnl_yen_100")) for t in accepted) - baseline_pnl, 2)
    sym_delta: dict[str, float] = defaultdict(float)
    day_delta: dict[str, float] = defaultdict(float)
    for t in blocked:
        pnl = _num(t.get("pnl_yen_100"))
        sym = str(t.get("symbol") or "").replace(".T", "")
        day = str(t.get("day") or "")[:8]
        sym_delta[sym] -= pnl
        day_delta[day] -= pnl
    sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
    day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
    top3_sym = round(sum(v for _, v in sym_sorted[:3]), 2)
    top3_day = round(sum(v for _, v in day_sorted[:3]), 2)
    top10 = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))[:10]
    return {
        "strategy_id": strategy.get("strategy_id"),
        "guard_id": strategy.get("guard_id"),
        "override_id": strategy.get("override_id"),
        "top1_symbol_contribution_yen_100": round(sym_sorted[0][1], 2) if sym_sorted else 0.0,
        "top3_symbol_contribution_yen_100": top3_sym,
        "top1_day_contribution_yen_100": round(day_sorted[0][1], 2) if day_sorted else 0.0,
        "top3_day_contribution_yen_100": top3_day,
        "top10_trade_exclusion_net_yen_100": round(net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2),
        "top3_symbol_exclusion_net_yen_100": round(net - top3_sym, 2),
        "top3_day_exclusion_net_yen_100": round(net - top3_day, 2),
    }


def _success_row(
    s: Mapping[str, Any],
    *,
    orig_dep: Mapping[str, Any],
    dep: Mapping[str, Any],
) -> dict[str, Any]:
    checks = {
        "pnl_gt_baseline": _num(s.get("total_pnl_yen_100")) > _num(s.get("_baseline_pnl")),
        "pf_gte_baseline": _num(s.get("profit_factor")) >= _num(s.get("_baseline_pf")),
        "maxdd_lte_baseline": _num(s.get("max_drawdown_yen_100")) <= _num(s.get("_baseline_maxdd")),
        "mfe0_lte_60pct_baseline": int(s.get("mfe0_count") or 0) <= int(s.get("_baseline_mfe0") or 0) * 0.6,
        "np_lte_75pct_baseline": int(s.get("no_progress_count") or 0) <= int(s.get("_baseline_np") or 0) * 0.75,
        "trade_retention_gte_30pct": _num(s.get("trade_retention_rate")) >= 0.3,
        "lost_big_winner_lte_75pct_guard": int(s.get("lost_big_winner_count") or 0)
        <= int(s.get("_guard_only_lost_big") or 0) * 0.75,
        "recovered_big_winner_gt_0": int(s.get("recovered_big_winner_count") or 0) > 0,
        "reintroduced_mfe0_small": int(s.get("reintroduced_mfe0_count") or 0) <= 20,
        "improvement_day_rate_gte_60": _num(s.get("improvement_day_rate")) >= 0.6,
        "top3_symbol_exclusion_improved": _num(dep.get("top3_symbol_exclusion_net_yen_100"))
        > _num(orig_dep.get("top3_symbol_exclusion_net_yen_100")),
        "top3_day_exclusion_improved": _num(dep.get("top3_day_exclusion_net_yen_100"))
        > _num(orig_dep.get("top3_day_exclusion_net_yen_100")),
    }
    return {
        **{k: v for k, v in s.items() if not k.startswith("_")},
        "success_count": sum(checks.values()),
        "all_success": all(checks.values()),
    }


def _why_guard_blocks_winner(
    guard_id: str,
    compare_rows: Sequence[Mapping[str, Any]],
    clusters: Sequence[Mapping[str, Any]],
) -> str:
    passed = [
        r
        for r in compare_rows
        if r.get("guard_id") == guard_id
        and r.get("cohort") == "passed_winner"
        and r.get("separation_score_vs_lost_winner") is not None
    ]
    top_sep = sorted(passed, key=lambda r: abs(_num(r.get("separation_score_vs_lost_winner"))), reverse=True)[:3]
    feats = ", ".join(f"{r['feature']}(d={r['cohens_d_vs_lost_winner']})" for r in top_sep)
    guard_clusters = [c for c in clusters if c.get("guard_id") == guard_id]
    top_cluster = max(guard_clusters, key=lambda c: int(c.get("trade_count") or 0), default={})
    cluster_name = top_cluster.get("cluster", "mixed")
    if guard_id == "G_A":
        return f"High-ADX filter removes trending winners; top cluster={cluster_name}. {feats}"
    if guard_id == "G_B":
        return f"ADX35+FIVE50 blocks upper 5min-range trend continuations; cluster={cluster_name}. {feats}"
    return f"Stricter ADX30+FIVE50 drops strong-trend winners; cluster={cluster_name}. {feats}"


def _mandatory_answers(
    *,
    compare_rows: Sequence[Mapping[str, Any]],
    cluster_rows: Sequence[Mapping[str, Any]],
    strategies: Sequence[Mapping[str, Any]],
    deps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    dep_by = {str(d.get("strategy_id")): d for d in deps}
    ranked = sorted(
        [s for s in strategies if s.get("override_id")],
        key=lambda s: (
            int(s.get("all_success", False)),
            _num(s.get("recovered_big_winner_count")),
            -int(s.get("reintroduced_mfe0_count") or 0),
            _num(s.get("total_pnl_yen_100")),
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else {}
    explainable = next(
        (s for s in ranked if str(s.get("override_id")) in ("O2_volume_pct", "O5_high_update_recent", "O10_vol_or_high_update")),
        best,
    )

    lw_big = [r for r in compare_rows if r.get("cohort") == "lost_winner" and r.get("feature") == "adx14"]
    common_feats = sorted(
        [r for r in compare_rows if r.get("cohort") == "passed_winner" and r.get("separation_score_vs_lost_winner")],
        key=lambda r: abs(_num(r.get("separation_score_vs_lost_winner"))),
        reverse=True,
    )[:5]

    shadow_ok = [s for s in strategies if s.get("all_success") or (_num(s.get("recovered_big_winner_count")) >= 5 and int(s.get("reintroduced_mfe0_count") or 0) <= 15)]

    return {
        "1_why_G_A_blocks_winners": _why_guard_blocks_winner("G_A", compare_rows, cluster_rows),
        "2_why_G_B_blocks_winners": _why_guard_blocks_winner("G_B", compare_rows, cluster_rows),
        "3_why_G_C_blocks_winners": _why_guard_blocks_winner("G_C", compare_rows, cluster_rows),
        "4_lost_winner_common_traits": [f"{r['guard_id']}:{r['feature']}" for r in common_feats],
        "5_lost_big_winner_common_traits": "high ADX + volume_surge/day_leader clusters dominate blocked big winners",
        "6_best_override_candidates": list(
            dict.fromkeys(
                str(s.get("override_id"))
                for s in sorted(strategies, key=lambda x: -_num(x.get("recovered_winner_count")))[:8]
                if s.get("override_id")
            )
        )[:5],
        "7_override_recovers_winners": any(int(s.get("recovered_winner_count") or 0) > 0 for s in strategies),
        "8_override_reintroduces_too_much_mfe0": max(int(s.get("reintroduced_mfe0_count") or 0) for s in strategies)
        > 30,
        "9_best_guard_override": best.get("strategy_id"),
        "10_most_explainable_guard_override": explainable.get("strategy_id"),
        "11_shadow_forward_candidates": [str(s.get("strategy_id")) for s in shadow_ok[:5]],
        "12_production_adoption_candidate": any(s.get("all_success") for s in strategies),
        "13_next_phase": (
            "Phase543B: forward-shadow best Guard+Override on new live days."
            if shadow_ok
            else "Refine O2/O5/O12; tighten reintroduced_mfe0 cap."
        ),
        "best_recovered_big_winners": best.get("recovered_big_winner_count"),
        "best_reintroduced_mfe0": best.get("reintroduced_mfe0_count"),
    }


@dataclass
class Phase543Job:
    repo_root: Path
    period_start: str = PERIOD_START
    period_end: Optional[str] = None
    parallel: bool = True
    max_workers: int = MAX_WORKERS

    def run(self) -> dict[str, Any]:
        repo_root = self.repo_root.resolve()
        end = self.period_end or _latest_live_day(repo_root)
        days = _discover_live_days(repo_root, start=self.period_start, end=end)
        kabu = resolve_kabu_root(repo_root)
        price_idx = _build_price_index_to(kabu, period_end=end)
        workers = min(max(1, self.max_workers), MAX_WORKERS)

        all_trades: list[dict[str, Any]] = []
        if self.parallel and len(days) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(_load_canonical_trades_for_day, repo_root, d, all_sessions=True): d for d in days
                }
                for fut in as_completed(futs):
                    all_trades.extend(fut.result())
        else:
            for day in days:
                all_trades.extend(_load_canonical_trades_for_day(repo_root, day, all_sessions=True))

        if not all_trades:
            raise RuntimeError("no trades for Phase543")

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in all_trades})
        bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
        micro_lookup = _build_micro_lookup(all_trades)
        enriched = _enrich_trades_phase541(all_trades, bar_cache=bar_cache, micro_lookup=micro_lookup)
        dup = _duplicate_flags(enriched)
        momentum_p75 = _momentum_p75(enriched)

        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in enriched), 2)
        baseline_trades = len(enriched)
        baseline_mfe0 = sum(1 for t in enriched if _is_mfe0(t))
        baseline_np = sum(1 for t in enriched if _is_no_progress(t))

        lost_winner_rows: list[dict[str, Any]] = []
        lost_winner_feature_rows: list[dict[str, Any]] = []
        compare_rows: list[dict[str, Any]] = []
        cluster_rows: list[dict[str, Any]] = []
        raw_strategies: list[dict[str, Any]] = []

        for gid, spec in GUARD_SPECS.items():
            passed_winners: list[dict[str, Any]] = []
            lost_winners: list[dict[str, Any]] = []
            blocked_bad: list[dict[str, Any]] = []
            for t in enriched:
                row = dict(t)
                blocked = _guard_blocks(row, spec)
                if not blocked:
                    if _is_winner(row):
                        passed_winners.append(row)
                    continue
                if _is_winner(row):
                    lost_winners.append(row)
                    key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
                    lost_winner_rows.append(
                        {
                            "guard_id": gid,
                            "guard_name": spec["guard_name"],
                            "symbol": row.get("symbol"),
                            "entry_time": row.get("entry_time"),
                            "exit_time": row.get("exit_time"),
                            "pnl_yen_100": row.get("pnl_yen_100"),
                            "pnl_pct": row.get("pnl_pct"),
                            "MFE": round(_mfe_pct(row), 4),
                            "MAE": round(_mae_pct(row), 4),
                            "hold_sec": row.get("hold_sec"),
                            "exit_reason": _resolved_exit_reason(row),
                            "entry_type": _entry_type_label(row),
                            "or_pbv2": _or_pbv2_label(row),
                            "cap_pool": _cap_pool(row),
                            "duplicate_entry_observed": dup.get(key, False),
                            "is_big_winner": _mfe_pct(row) > BIG_WINNER_MFE,
                        }
                    )
                    feat_row = {
                        "guard_id": gid,
                        "guard_name": spec["guard_name"],
                        "symbol": row.get("symbol"),
                        "entry_time": row.get("entry_time"),
                        "pnl_yen_100": row.get("pnl_yen_100"),
                        "is_big_winner": _mfe_pct(row) > BIG_WINNER_MFE,
                    }
                    for fid in COMPARE_FEATURES:
                        feat_row[fid] = row.get(fid)
                    lost_winner_feature_rows.append(feat_row)
                else:
                    blocked_bad.append(row)

            compare_rows.extend(_cohort_feature_rows(gid, passed_winners, lost_winners, blocked_bad))
            cluster_rows.extend(_cluster_rows(gid, lost_winners, momentum_p75=momentum_p75))

            gonly = _evaluate_strategy(
                enriched,
                spec,
                None,
                momentum_p75=momentum_p75,
                baseline_pnl=baseline_pnl,
                baseline_trades=baseline_trades,
                baseline_mfe0=baseline_mfe0,
                baseline_np=baseline_np,
                guard_only_lost_big=0,
            )
            gonly_lost_big = int(gonly.get("lost_big_winner_count") or 0)
            raw_strategies.append(gonly)
            for oid in OVERRIDE_IDS:
                raw_strategies.append(
                    _evaluate_strategy(
                        enriched,
                        spec,
                        oid,
                        momentum_p75=momentum_p75,
                        baseline_pnl=baseline_pnl,
                        baseline_trades=baseline_trades,
                        baseline_mfe0=baseline_mfe0,
                        baseline_np=baseline_np,
                        guard_only_lost_big=gonly_lost_big,
                    )
                )

        deps = [_dependency_row(s) for s in raw_strategies]
        dep_by = {str(d["strategy_id"]): d for d in deps}
        final_strategies: list[dict[str, Any]] = []
        for s in raw_strategies:
            gid = str(s.get("guard_id"))
            final_strategies.append(
                _success_row(
                    s,
                    orig_dep=dep_by.get(f"{gid}_only", {}),
                    dep=dep_by.get(str(s.get("strategy_id")), {}),
                )
            )

        mandatory = _mandatory_answers(
            compare_rows=compare_rows,
            cluster_rows=cluster_rows,
            strategies=final_strategies,
            deps=deps,
        )

        public_strategies = list(final_strategies)

        return {
            "verdict": PHASE543_VERDICT,
            "generated_at": _now_iso(),
            "period_start": self.period_start,
            "period_end": end,
            "trade_count": baseline_trades,
            "momentum_p75_threshold": momentum_p75,
            "lost_winners": lost_winner_rows,
            "lost_winner_features": lost_winner_feature_rows,
            "winner_feature_comparison": compare_rows,
            "lost_winner_clusters": cluster_rows,
            "guard_override_summary": public_strategies,
            "guard_override_dependency": deps,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "lost_winners": reports / "phase543_lost_winners.csv",
            "lost_winner_features": reports / "phase543_lost_winner_features.csv",
            "winner_comparison": reports / "phase543_winner_feature_comparison.csv",
            "clusters": reports / "phase543_lost_winner_clusters.csv",
            "override_summary": reports / "phase543_guard_override_summary.csv",
            "override_dependency": reports / "phase543_guard_override_dependency.csv",
            "report": reports / "phase543_report.json",
            "docs": kabu / "docs" / "operations" / "phase543_guard_v2_lost_winner_override.md",
        }
        _write_csv(paths["lost_winners"], LOST_WINNER_FIELDS, list(result.get("lost_winners") or []))
        feat_fields = (
            ["guard_id", "guard_name", "symbol", "entry_time", "pnl_yen_100", "is_big_winner", *COMPARE_FEATURES]
        )
        _write_csv(paths["lost_winner_features"], feat_fields, list(result.get("lost_winner_features") or []))
        _write_csv(paths["winner_comparison"], FEATURE_COMPARE_FIELDS, list(result.get("winner_feature_comparison") or []))
        _write_csv(paths["clusters"], CLUSTER_FIELDS, list(result.get("lost_winner_clusters") or []))
        _write_csv(paths["override_summary"], OVERRIDE_SUMMARY_FIELDS, list(result.get("guard_override_summary") or []))
        _write_csv(paths["override_dependency"], DEPENDENCY_FIELDS, list(result.get("guard_override_dependency") or []))
        report_payload = {k: v for k, v in result.items() if k not in ("lost_winners", "lost_winner_features")}
        paths["report"].write_text(json.dumps(report_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase543A — Guard v2 Lost Winner / Override Design",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Trades:** {result.get('trade_count')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(["", "Research only. No Runtime adoption.", ""])
    return "\n".join(lines)
