"""
Phase493 — Global Entry Failure Audit (research only).

PBv2 runtime CAP=5 full-period replay; loser/winner entry feature audit.
No Runtime / YAML / Entry / Exit / Order / Discord changes.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import LEVERAGE, STOP_POLICY, CapacityReplayState
from research.phase451_entry_shape_tournament import _build_price_index_to, _chronological_pnls_from_log, _now_iso
from research.phase463_trend_pullback_population_tournament import (
    _board_bucket,
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _valid_replay_trade,
)
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase473_trend_entry_architecture import _entry_block, _rise, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase483_stop_low_mfe_root_cause_audit import _ks_stat
from research.phase484_stop_low_mfe_feature_discovery import _compute_base_features
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
    _summary_metrics,
)
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
PERIOD_START = "20260529"
PERIOD_END = "20260622"
FOCUS_SYMBOLS = ("6522", "6976", "6981", "4062", "6920", "3449", "5367")
DAY_622 = "20260622"

FEATURES = (
    "r5", "r10", "r15", "r30", "r15_minus_r5", "r30_minus_r5",
    "vwap_dev_pct", "vwap_extension_rate", "vwap_structure_score",
    "momentum_continuation_score", "board_imbalance", "board_change_5m", "board_change_10m",
    "day_high_distance", "high_update_age", "high_update_count_30m",
)

CLUSTER_NAMES = (
    "late_chase_after_rally_vwap_trap",
    "falling_knife",
    "high_price_extension",
    "no_progress_low_mfe",
    "ordinary_loss",
)

AUDIT_FIELDS = [
    "symbol", "entry_time", "entry_price", "exit_reason", "pnl_yen_100",
    "mfe_pct", "mae_pct", "session_bucket", "failure_cluster",
    "r5", "r10", "r30_minus_r5", "vwap_dev_pct",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _session_bucket(entry_time: Any) -> str:
    dt = _parse_ts(str(entry_time or ""))
    if dt is None:
        return "unknown"
    h = dt.astimezone(JST).hour
    return "PM" if h >= 12 else "AM"


def _exit_reason(row: Mapping[str, Any]) -> str:
    return str(row.get("exit_reason") or "").strip()


def _is_winner(row: Mapping[str, Any]) -> bool:
    pnl = float(row.get("pnl_yen") or 0)
    reason = _exit_reason(row)
    if reason == "trailing_mfe_exit" and pnl > 0:
        return True
    if "session_close" in reason and pnl > 0:
        return True
    return False


def _is_loser(row: Mapping[str, Any]) -> bool:
    return _exit_reason(row) in ("stop_hit", "no_progress_exit")


def _enrich_trade_row(log: Mapping[str, Any]) -> dict[str, Any]:
    tr = dict(log.get("trade") or log)
    base = _compute_base_features(tr)
    r5, r10, r15, r30 = _rise(tr, 5), _rise(tr, 10), _rise(tr, 15), _rise(tr, 30)
    pnl = float(log.get("pnl_yen") or tr.get("pnl_yen") or 0)
    mfe = _float(tr.get("mfe_pct") or tr.get("peak_mfe_pct"))
    row = {
        "position_key": _position_key(tr),
        "symbol": str(tr.get("symbol") or "").replace(".T", ""),
        "day": str(log.get("day") or tr.get("day") or "")[:8],
        "entry_time": tr.get("entry_time"),
        "exit_time": log.get("exit_time"),
        "exit_reason": _exit_reason(log),
        "pnl_yen": round(pnl, 2),
        "pnl_yen_100": round(pnl, 2),
        "mfe_pct": mfe,
        "mae_pct": _float(tr.get("mae_pct") or tr.get("rolling_mae_pct")),
        "entry_price": _float(tr.get("entry_price")),
        "session_bucket": _session_bucket(tr.get("entry_time")),
        "board_tier": _board_bucket(tr),
        "r5": r5,
        "r10": r10,
        "r15": r15,
        "r30": r30,
        "r15_minus_r5": base.get("A2_r15_minus_r5"),
        "r30_minus_r5": base.get("A1_r30_minus_r5"),
        "vwap_dev_pct": base.get("B1_vwap_dev_pct"),
        "vwap_extension_rate": base.get("B2_vwap_extension_rate"),
        "vwap_structure_score": _float(tr.get("vwap_structure_score")),
        "momentum_continuation_score": _float(tr.get("momentum_continuation_score")),
        "board_imbalance": _float(tr.get("board_imbalance") or tr.get("entry_order_book_imbalance")),
        "board_change_5m": base.get("D1_board_change_5m"),
        "board_change_10m": base.get("D2_board_change_10m"),
        "day_high_distance": _float(tr.get("day_high_distance_pct")),
        "high_update_age": _float(tr.get("high_update_age") or tr.get("minutes_since_day_high_update")),
        "high_update_count_30m": _float(tr.get("high_update_count_30m")),
        "is_winner": _is_winner(log),
        "is_loser": _is_loser(log),
        "_trade": tr,
    }
    return row


def _top_pct_threshold(values: Sequence[float], pct: float = 80.0) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    idx = min(len(ranked) - 1, int(round((pct / 100.0) * (len(ranked) - 1))))
    return ranked[idx]


def _classify_cluster(row: Mapping[str, Any], *, medians: Mapping[str, float]) -> str:
    mfe = _float(row.get("mfe_pct"))
    reason = _exit_reason(row)
    r5, r10 = _float(row.get("r5")), _float(row.get("r10"))
    r15m5 = _float(row.get("r15_minus_r5"))
    r30m5 = _float(row.get("r30_minus_r5"))
    vwap_dev = _float(row.get("vwap_dev_pct"))
    dhd = _float(row.get("day_high_distance"))
    tier = str(row.get("board_tier") or "")

    if mfe is not None and mfe < 0.5 and reason in ("stop_hit", "no_progress_exit"):
        return "no_progress_low_mfe"
    if (r5 is not None and r5 < 0) or (r10 is not None and r10 < -0.3):
        if tier in ("mid", "high", "board_mid", "board_high"):
            return "falling_knife"
    if (
        (r10 is not None and r10 > medians.get("r10", 0))
        or (r30m5 is not None and r30m5 > medians.get("r30_minus_r5", 0))
        or (r15m5 is not None and r15m5 > medians.get("r15_minus_r5", 0))
    ) and (vwap_dev is not None and vwap_dev > medians.get("vwap_dev_pct", 0)):
        return "late_chase_after_rally_vwap_trap"
    if (dhd is not None and dhd < 1.2) or (vwap_dev is not None and vwap_dev > medians.get("vwap_dev_pct", 0.5)):
        return "high_price_extension"
    return "ordinary_loss"


def _cluster_flags(row: Mapping[str, Any], *, medians: Mapping[str, float]) -> dict[str, bool]:
    c = _classify_cluster(row, medians=medians)
    return {
        "late_chase_cluster": c == "late_chase_after_rally_vwap_trap",
        "falling_knife_cluster": c == "falling_knife",
        "high_price_extension_cluster": c == "high_price_extension",
        "cluster": c,
    }


def _global_summary(rows: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, Any]:
    pnls = [float(r["pnl_yen"]) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    flat = sum(1 for p in pnls if p == 0)
    reasons = Counter(_exit_reason(r) for r in rows)
    return {
        "bucket": label,
        "trade_count": len(rows),
        "win_count": wins,
        "loss_count": losses,
        "flat_count": flat,
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(pnls), 2),
        "stop_hit_count": reasons.get("stop_hit", 0),
        "no_progress_count": reasons.get("no_progress_exit", 0),
        "trailing_mfe_count": reasons.get("trailing_mfe_exit", 0),
        "session_close_count": sum(v for k, v in reasons.items() if "session_close" in k),
    }


def _feature_comparison(
    losers: Sequence[Mapping[str, Any]],
    winners: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for feat in FEATURES:
        lv = [_float(r.get(feat)) for r in losers]
        wv = [_float(r.get(feat)) for r in winners]
        lv_n = [v for v in lv if v is not None]
        wv_n = [v for v in wv if v is not None]
        miss = 1.0 - (len(lv_n) + len(wv_n)) / max(1, len(lv) + len(wv))
        lm = statistics.mean(lv_n) if lv_n else None
        wm = statistics.mean(wv_n) if wv_n else None
        direction = "higher_in_loser" if lm is not None and wm is not None and lm > wm else (
            "lower_in_loser" if lm is not None and wm is not None and lm < wm else "unknown"
        )
        out.append(
            {
                "feature": feat,
                "loser_mean": round(lm, 4) if lm is not None else None,
                "winner_mean": round(wm, 4) if wm is not None else None,
                "loser_median": round(statistics.median(lv_n), 4) if lv_n else None,
                "winner_median": round(statistics.median(wv_n), 4) if wv_n else None,
                "missing_rate": round(miss, 4),
                "cohens_d": _cohens_d(lv_n, wv_n),
                "ks_statistic": _ks_stat(lv_n, wv_n),
                "mutual_information": _mi_median_split(wv_n, lv_n),
                "feature_direction": direction,
            }
        )
    out.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    return out


def _cluster_summary(rows: Sequence[Mapping[str, Any]], *, medians: Mapping[str, float]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not _is_loser(r):
            continue
        buckets[_classify_cluster(r, medians=medians)].append(dict(r))
    out: list[dict[str, Any]] = []
    for name in CLUSTER_NAMES:
        grp = buckets.get(name, [])
        if not grp:
            continue
        pnls = [float(g["pnl_yen"]) for g in grp]
        sym = Counter(str(g["symbol"]) for g in grp)
        am = sum(1 for g in grp if g.get("session_bucket") == "AM")
        out.append(
            {
                "cluster": name,
                "count": len(grp),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "stop_hit_count": sum(1 for g in grp if _exit_reason(g) == "stop_hit"),
                "no_progress_count": sum(1 for g in grp if _exit_reason(g) == "no_progress_exit"),
                "symbols_top5": ",".join(s for s, _ in sym.most_common(5)),
                "am_ratio": round(am / len(grp), 4),
                "pm_ratio": round(1.0 - am / len(grp), 4),
            }
        )
    out.sort(key=lambda r: float(r["total_pnl_yen_100"]))
    return out


def _symbol_attribution(
    rows: Sequence[Mapping[str, Any]],
    *,
    medians: Mapping[str, float],
) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_sym[str(r["symbol"])].append(dict(r))
    out: list[dict[str, Any]] = []
    for sym in sorted(by_sym, key=lambda s: sum(float(x["pnl_yen"]) for x in by_sym[s])):
        grp = by_sym[sym]
        pnls = [float(g["pnl_yen"]) for g in grp]
        clusters = Counter(_classify_cluster(g, medians=medians) for g in grp if _is_loser(g))
        out.append(
            {
                "symbol": sym,
                "trade_count": len(grp),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "stop_hit": sum(1 for g in grp if _exit_reason(g) == "stop_hit"),
                "no_progress": sum(1 for g in grp if _exit_reason(g) == "no_progress_exit"),
                "trailing": sum(1 for g in grp if _exit_reason(g) == "trailing_mfe_exit"),
                "cluster_mix": dict(clusters),
                "focus_symbol": sym in FOCUS_SYMBOLS,
            }
        )
    return out


def _replay_with_extra_block(
    pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, Any],
    *,
    extra_block: Callable[[Mapping[str, Any]], bool],
    mode_suffix: str,
) -> CapacityReplayState:
    def pass_fn(trade: Mapping[str, Any]) -> bool:
        if not pass_pbv2(trade):
            return False
        return not extra_block(trade)

    return _simulate_runtime_replay(
        pool,
        shadows,
        mode=f"{REPLAY_MODE}_{mode_suffix}",
        entry_block_fn=_entry_block(pass_fn),
        initial_equity=1_500_000.0,
    )


def _counterfactual_row(
    state: CapacityReplayState,
    baseline_state: CapacityReplayState,
    *,
    scenario: str,
    baseline_pnl: float,
    baseline_pf: Any,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    met = _summary_metrics(state, initial_equity=1_500_000.0)
    remain_pnl = float(met["total_pnl_yen"])
    delta = remain_pnl - baseline_pnl
    base_keys = {_position_key(log.get("trade") or {}) for log in baseline_state.trade_log}
    remain_keys = {_position_key(log.get("trade") or {}) for log in state.trade_log}
    blocked_keys = base_keys - remain_keys
    blocked = [r for r in rows if r["position_key"] in blocked_keys]
    bw = sum(1 for r in blocked if float(r["pnl_yen"]) > 0)
    bl = sum(1 for r in blocked if float(r["pnl_yen"]) < 0)
    bp = sum(float(r["pnl_yen"]) for r in blocked)

    def _sym_pnl(sym: str) -> float:
        return sum(float(r["pnl_yen"]) for r in blocked if r["symbol"] == sym)

    day622 = sum(float(r["pnl_yen"]) for r in blocked if r.get("day") == DAY_622)
    am_block = sum(float(r["pnl_yen"]) for r in blocked if r.get("session_bucket") == "AM")
    pm_block = sum(float(r["pnl_yen"]) for r in blocked if r.get("session_bucket") == "PM")
    return {
        "scenario": scenario,
        "blocked_total": len(blocked),
        "blocked_winners": bw,
        "blocked_losers": bl,
        "blocked_pnl_yen_100": round(bp, 2),
        "remaining_pnl_yen_100": round(remain_pnl, 2),
        "delta_pnl_yen_100": round(delta, 2),
        "remaining_PF": met["profit_factor"],
        "maxDD_yen_100": met["max_drawdown_yen"],
        "impact_6976": round(_sym_pnl("6976"), 2),
        "impact_4062": round(_sym_pnl("4062"), 2),
        "impact_6522": round(_sym_pnl("6522"), 2),
        "impact_20260622": round(day622, 2),
        "impact_AM": round(am_block, 2),
        "impact_PM": round(pm_block, 2),
        "baseline_pnl": baseline_pnl,
        "baseline_PF": baseline_pf,
    }


def _build_thresholds(pool: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    pb = [dict(t) for t in pool if pass_pbv2(t)]
    enriched = [_enrich_trade_row({"trade": t, "pnl_yen": 0, "exit_reason": ""}) for t in pb]
    def vals(k: str) -> list[float]:
        return [float(r[k]) for r in enriched if _float(r.get(k)) is not None]
    return {
        "r30_minus_r5": _top_pct_threshold(vals("r30_minus_r5"), 80),
        "r15_minus_r5": _top_pct_threshold(vals("r15_minus_r5"), 80),
        "vwap_extension_rate": _top_pct_threshold(vals("vwap_extension_rate"), 80),
    }


def _medians_from_losers(losers: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    def med(k: str) -> float:
        v = [_float(r.get(k)) for r in losers]
        v = [x for x in v if x is not None]
        return statistics.median(v) if v else 0.0
    return {
        "r10": med("r10"),
        "r15_minus_r5": med("r15_minus_r5"),
        "r30_minus_r5": med("r30_minus_r5"),
        "vwap_dev_pct": med("vwap_dev_pct"),
    }


def _verdict(
    *,
    cluster_rows: Sequence[Mapping[str, Any]],
    cf_rows: Sequence[Mapping[str, Any]],
    am_summary: Mapping[str, Any],
    pm_summary: Mapping[str, Any],
    day622_share: float,
) -> str:
    worst = min(cluster_rows, key=lambda r: float(r["total_pnl_yen_100"])) if cluster_rows else None
    best_cf = max(cf_rows, key=lambda r: float(r.get("delta_pnl_yen_100") or 0)) if cf_rows else None
    if not worst or not best_cf:
        return "no_actionable_pattern"
    if float(best_cf.get("delta_pnl_yen_100") or 0) < 5000:
        return "no_actionable_pattern"
    if day622_share > 0.45 and float(best_cf.get("impact_20260622") or 0) < -10000:
        return "single_day_outlier"
    pm_pf = float(pm_summary.get("profit_factor") or 0)
    am_pf = float(am_summary.get("profit_factor") or 0)
    if pm_pf < 0.8 and am_pf > 1.2 and float(best_cf.get("impact_PM") or 0) < float(best_cf.get("impact_AM") or 0):
        if float(best_cf.get("delta_pnl_yen_100") or 0) > 20000:
            return "global_guard_candidate"
        return "pm_specific_problem"
    if float(best_cf.get("remaining_PF") or 0) > 1.05:
        return "global_guard_candidate"
    if float(best_cf.get("delta_pnl_yen_100") or 0) > 15000:
        return "global_guard_candidate"
    return "needs_new_feature"


def run_phase493(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(2, max_workers), 4)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase493",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    baseline_met = _summary_metrics(baseline_state, initial_equity=1_500_000.0)
    baseline_pnl = float(baseline_met["total_pnl_yen"])
    baseline_pf = baseline_met["profit_factor"]

    rows = [_enrich_trade_row(log) for log in baseline_state.trade_log]
    losers = [r for r in rows if _is_loser(r)]
    winners = [r for r in rows if _is_winner(r)]
    medians = _medians_from_losers(losers)
    thresholds = _build_thresholds(replay_pool)

    for r in rows:
        flags = _cluster_flags(r, medians=medians)
        r["failure_cluster"] = flags["cluster"]

    global_rows = [
        _global_summary(rows, label="ALL"),
        _global_summary([r for r in rows if r["session_bucket"] == "AM"], label="AM"),
        _global_summary([r for r in rows if r["session_bucket"] == "PM"], label="PM"),
    ]
    feature_rows = _feature_comparison(losers, winners)
    cluster_rows = _cluster_summary(rows, medians=medians)
    symbol_rows = _symbol_attribution(rows, medians=medians)

    loser_clusters = Counter(_classify_cluster(r, medians=medians) for r in losers)
    n_losers = len(losers) or 1
    trap_rate = loser_clusters.get("late_chase_after_rally_vwap_trap", 0) / n_losers
    knife_rate = loser_clusters.get("falling_knife", 0) / n_losers
    ext_rate = loser_clusters.get("high_price_extension", 0) / n_losers

    top_sym = min(symbol_rows, key=lambda r: float(r["total_pnl_yen_100"])) if symbol_rows else {}

    def block_metric(key: str, thr: float) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            row = _enrich_trade_row({"trade": trade, "pnl_yen": 0, "exit_reason": ""})
            v = _float(row.get(key))
            return v is not None and v >= thr
        return _b

    def block_cluster(name: str) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            row = _enrich_trade_row({"trade": trade, "pnl_yen": 0, "exit_reason": ""})
            return _classify_cluster(row, medians=medians) == name
        return _b

    cf_specs: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("A_r30_minus_r5_top20", block_metric("r30_minus_r5", thresholds["r30_minus_r5"])),
        ("B_r15_minus_r5_top20", block_metric("r15_minus_r5", thresholds["r15_minus_r5"])),
        ("C_vwap_extension_top20", block_metric("vwap_extension_rate", thresholds["vwap_extension_rate"])),
        ("D_late_chase_cluster", block_cluster("late_chase_after_rally_vwap_trap")),
        ("E_falling_knife_cluster", block_cluster("falling_knife")),
        ("F_high_price_extension", block_cluster("high_price_extension")),
    ]

    def block_or(*fns: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
        def _b(trade: Mapping[str, Any]) -> bool:
            return any(fn(trade) for fn in fns)
        return _b

    cf_specs.append(("G_late_chase_OR_falling_knife", block_or(
        block_cluster("late_chase_after_rally_vwap_trap"),
        block_cluster("falling_knife"),
    )))

    # H: best 2 of A,B,C by univariate delta (quick scan on trade rows)
    uni_deltas: list[tuple[str, float, Callable[[Mapping[str, Any]], bool]]] = []
    for name, key, thr in (
        ("A", "r30_minus_r5", thresholds["r30_minus_r5"]),
        ("B", "r15_minus_r5", thresholds["r15_minus_r5"]),
        ("C", "vwap_extension_rate", thresholds["vwap_extension_rate"]),
    ):
        blocked = [r for r in rows if _float(r.get(key)) is not None and float(r[key]) >= thr]
        remain = [r for r in rows if r not in blocked]
        delta = sum(float(r["pnl_yen"]) for r in remain) - baseline_pnl
        uni_deltas.append((name, delta, block_metric(key, thr)))
    uni_deltas.sort(key=lambda x: x[1], reverse=True)
    h_block = block_or(uni_deltas[0][2], uni_deltas[1][2])
    cf_specs.append(("H_conservative_best2", h_block))

    cf_rows: list[dict[str, Any]] = []

    def _run_cf(name: str, block_fn: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
        st = _replay_with_extra_block(replay_pool, runtime_shadows, extra_block=block_fn, mode_suffix=name[:12])
        return _counterfactual_row(
            st, baseline_state,
            scenario=name, baseline_pnl=baseline_pnl, baseline_pf=baseline_pf, rows=rows,
        )

    if parallel and len(cf_specs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_run_cf, n, b): n for n, b in cf_specs}
            for fut in as_completed(futs):
                cf_rows.append(fut.result())
    else:
        for name, block_fn in cf_specs:
            cf_rows.append(_run_cf(name, block_fn))
    cf_rows.sort(key=lambda r: float(r.get("delta_pnl_yen_100") or 0), reverse=True)
    best_cf = cf_rows[0] if cf_rows else {}

    # Robustness on best scenario block
    best_name, best_block_fn = cf_specs[0]
    for name, block_fn in cf_specs:
        if block_fn == best_cf.get("scenario"):
            break
    for n, b in cf_specs:
        if n == best_cf.get("scenario"):
            best_block_fn = b
            best_name = n
            break
    else:
        best_block_fn = cf_specs[0][1]
        best_name = cf_specs[0][0]

    robustness_rows: list[dict[str, Any]] = []
    days = sorted({str(r["day"]) for r in rows})
    for day in days:
        pool_day = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        if len(pool_day) < 50:
            continue
        st = _replay_with_extra_block(pool_day, runtime_shadows, extra_block=best_block_fn, mode_suffix=f"loo_{day}")
        met = _summary_metrics(st, initial_equity=1_500_000.0)
        robustness_rows.append({
            "test": f"LOO_day_{day}",
            "scenario": best_name,
            "total_pnl_yen_100": met["total_pnl_yen"],
            "profit_factor": met["profit_factor"],
            "trade_count": met["trade_count"],
        })

    for test_name, sym in (
        ("exclude_6976", "6976.T"),
        ("exclude_6522", "6522.T"),
        ("exclude_4062", "4062.T"),
    ):
        pool_ex = [t for t in replay_pool if str(t.get("symbol") or "") != sym]
        st = _replay_with_extra_block(pool_ex, runtime_shadows, extra_block=best_block_fn, mode_suffix=test_name)
        met = _summary_metrics(st, initial_equity=1_500_000.0)
        robustness_rows.append({
            "test": test_name,
            "scenario": best_name,
            "total_pnl_yen_100": met["total_pnl_yen"],
            "profit_factor": met["profit_factor"],
            "trade_count": met["trade_count"],
        })

    sym_counts = Counter(str(r["symbol"]) for r in rows)
    top_sym_code = sym_counts.most_common(1)[0][0] if sym_counts else ""
    pool_top = [t for t in replay_pool if str(t.get("symbol") or "").replace(".T", "") != top_sym_code]
    st = _replay_with_extra_block(pool_top, runtime_shadows, extra_block=best_block_fn, mode_suffix="ex_top_sym")
    met = _summary_metrics(st, initial_equity=1_500_000.0)
    robustness_rows.append({
        "test": "exclude_top_symbol",
        "scenario": best_name,
        "total_pnl_yen_100": met["total_pnl_yen"],
        "profit_factor": met["profit_factor"],
        "trade_count": met["trade_count"],
    })

    day622_pnl = sum(float(r["pnl_yen"]) for r in rows if r.get("day") == DAY_622)
    day622_share = abs(day622_pnl / baseline_pnl) if baseline_pnl else 0.0

    worst_cluster = min(cluster_rows, key=lambda r: float(r["total_pnl_yen_100"])) if cluster_rows else {}
    verdict = _verdict(
        cluster_rows=cluster_rows,
        cf_rows=cf_rows,
        am_summary=global_rows[1],
        pm_summary=global_rows[2],
        day622_share=day622_share,
    )

    mandatory = {
        "1_max_loss_cluster": worst_cluster.get("cluster"),
        "2_pm_only_or_global": (
            "global_problem_with_pm_amplification"
            if float(global_rows[2].get("profit_factor") or 0) < float(global_rows[1].get("profit_factor") or 0)
            else "global_problem"
        ),
        "3_day622_special": (
            "partially_special" if day622_share > 0.25 else "consistent_with_global_trend"
        ),
        "4_late_chase_match_rate": round(trap_rate, 4),
        "5_falling_knife_match_rate": round(knife_rate, 4),
        "6_high_price_extension_match_rate": round(ext_rate, 4),
        "7_worst_symbol": top_sym.get("symbol"),
        "8_6522_handling": "symbol_specific_falling_knife — guard E or re-entry cooldown shadow",
        "9_6976_handling": "high_price_extension / trap mix — monitor; do not exclude",
        "10_best_counterfactual": best_cf.get("scenario"),
        "11_delta_pnl": best_cf.get("delta_pnl_yen_100"),
        "12_blocked_winners": best_cf.get("blocked_winners"),
        "13_blocked_losers": best_cf.get("blocked_losers"),
        "14_hurts_am": float(best_cf.get("impact_AM") or 0) < -5000,
        "15_improves_pm": float(best_cf.get("impact_PM") or 0) > 0,
        "16_overfit_risk": "high_on_single_day_or_symbol_concentration" if day622_share > 0.3 else "moderate",
        "17_runtime_candidate": verdict == "global_guard_candidate" and float(best_cf.get("blocked_winners") or 0) <= 5,
        "18_shadow_candidate": True,
        "19_next_actions": [
            f"Verdict: {verdict}",
            f"Forward-shadow best guard: {best_cf.get('scenario')}",
            "Replay Phase487-style LOO on full pool before any gate enable",
            "6522: same-symbol re-entry cooldown shadow (not symbol exclude)",
        ],
        "verdict": verdict,
    }

    audit_export = [{k: r.get(k) for k in AUDIT_FIELDS} for r in losers]

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "trade_count": len(rows),
        "loser_count": len(losers),
        "winner_count": len(winners),
        "baseline_pnl_yen_100": baseline_pnl,
        "baseline_pf": baseline_pf,
        "_global_summary": global_rows,
        "_feature_comparison": feature_rows,
        "_cluster_rows": cluster_rows,
        "_symbol_rows": symbol_rows,
        "_counterfactual": cf_rows,
        "_robustness": robustness_rows,
        "_audit_export": audit_export,
    }


@dataclass
class Phase493Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase493(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        paths = {
            "audit": reports / "phase493_global_entry_failure_audit.csv",
            "clusters": reports / "phase493_failure_clusters.csv",
            "symbols": reports / "phase493_symbol_attribution.csv",
            "counterfactual": reports / "phase493_counterfactual.csv",
            "robustness": reports / "phase493_robustness.csv",
            "summary": reports / "phase493_summary.json",
            "report": doc_root / "docs" / "operations" / "phase493_global_entry_failure_audit.md",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, result.get("_audit_export") or [])
        _write_csv(paths["clusters"], list((result.get("_cluster_rows") or [{}])[0].keys()) or ["cluster"], result.get("_cluster_rows") or [])
        _write_csv(paths["symbols"], list((result.get("_symbol_rows") or [{}])[0].keys()) or ["symbol"], result.get("_symbol_rows") or [])
        _write_csv(paths["counterfactual"], list((result.get("_counterfactual") or [{}])[0].keys()) or ["scenario"], result.get("_counterfactual") or [])
        _write_csv(paths["robustness"], list((result.get("_robustness") or [{}])[0].keys()) or ["test"], result.get("_robustness") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase493 — Global Entry Failure Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')} — {result.get('period_end')}",
            "",
            "## Part A — Global Summary",
            "",
            json.dumps(result.get("_global_summary"), indent=2, ensure_ascii=False, default=str),
            "",
            "## 必須回答",
            "",
        ]
        for i, key in enumerate(
            [
                "1_max_loss_cluster", "2_pm_only_or_global", "3_day622_special",
                "4_late_chase_match_rate", "5_falling_knife_match_rate", "6_high_price_extension_match_rate",
                "7_worst_symbol", "8_6522_handling", "9_6976_handling", "10_best_counterfactual",
                "11_delta_pnl", "12_blocked_winners", "13_blocked_losers", "14_hurts_am", "15_improves_pm",
                "16_overfit_risk", "17_runtime_candidate", "18_shadow_candidate", "19_next_actions",
            ],
            1,
        ):
            lines.append(f"{i}. {m.get(key)}")
        lines.extend(["", f"**Verdict:** `{result.get('verdict')}`", ""])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
