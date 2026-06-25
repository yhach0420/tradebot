"""
Phase547 — V6 Reject cluster winner rescue analysis (research only).

Analyzes blocked V6 trades and designs exception rules to recover winners/big winners.
No Runtime changes. No adoption.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase518_day_high_winner_loser_separation import _percentile, _separation_score
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _num
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _is_no_progress, _is_winner, _mfe_pct
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase545_entry_pattern_clustering import _cluster_id_val
from research.phase545b_recursive_cluster_refinement import _as_bool
from research.phase546_entry_cluster_shadow_replay import (
    VARIANTS,
    _is_big_winner_row,
    _is_rejected,
    _merge_dataset,
    _trade_key,
    _csub_id,
    _subcluster_id,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE547_VERDICT = "phase547_reject_cluster_winner_rescue_done"
V6_SPEC = next(v for v in VARIANTS if v.variant_id == "V6")
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT

SEPARATION_FEATURES: tuple[str, ...] = (
    "board_imbalance",
    "relative_volume",
    "volume_accel_1m",
    "volume_accel_3m",
    "volume_accel_5m",
    "momentum_decay_1m",
    "momentum_decay_3m",
    "momentum_decay_5m",
    "exhaustion_score",
    "liquidity_burst",
    "day_return_rank",
    "volume_percentile",
    "tick_speed",
    "board_update_frequency",
    "update_count_before_entry",
    "high_update_recent",
    "prior_high_break",
    "vwap_distance_pct",
    "vwap_above_sec",
    "vwap_recovery_min",
    "price_acceleration",
    "return_since_open",
    "five_min_position",
    "adx14",
    "rsi14",
)

POPULATION_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "cluster_id",
    "subcluster_id",
    "new_subcluster_id",
    "reject_reason",
    "pnl_yen_100",
    "mfe_pct",
    "is_loser",
    "is_winner",
    "is_big_winner",
    "is_mfe0",
    "is_stop_low_mfe",
    "is_no_progress",
    "entry_type",
    "pbv2_or",
    "board_imbalance",
    "volume_percentile",
    "relative_volume",
    "day_return_rank",
]

SEPARATION_FIELDS = [
    "feature",
    "winner_count",
    "loser_count",
    "median_winner",
    "median_loser",
    "p25_winner",
    "p75_winner",
    "p25_loser",
    "p75_loser",
    "missing_rate_winner",
    "missing_rate_loser",
    "cohens_d",
    "separation_score",
]

BIG_WINNER_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "cluster_id",
    "new_subcluster_id",
    "reject_reason",
    "pnl_yen_100",
    "mfe_pct",
    "board_imbalance",
    "volume_percentile",
    "relative_volume",
    "liquidity_burst",
    "update_count_before_entry",
    "high_update_recent",
    "day_return_rank",
    "open_strength_proxy",
    "entry_type",
    "pbv2_or",
    "rescue_signal_summary",
]

EXCEPTION_CANDIDATE_FIELDS = [
    "exception_id",
    "label",
    "rule",
    "threshold_note",
    "rescued_count",
    "rescued_winner_count",
    "rescued_big_winner_count",
    "rescued_loser_count",
    "rescued_mfe0_count",
    "rescued_pnl_yen_100",
    "rescued_loser_pnl_yen_100",
]

REPLAY_FIELDS = [
    "variant_id",
    "exception_id",
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
    "recovered_winner_count",
    "recovered_big_winner_count",
    "reintroduced_loser_count",
    "reintroduced_mfe0_count",
    "reintroduced_loss_pnl_yen_100",
    "recovered_winner_pnl_yen_100",
    "net_improvement_vs_baseline_yen_100",
    "net_improvement_vs_v6_yen_100",
    "trade_retention_rate",
    "success_score",
    "runtime_candidate",
]

DEPENDENCY_FIELDS = [
    "exception_id",
    "top10_trade_exclusion_pnl_yen_100",
    "top3_symbol_exclusion_pnl_yen_100",
    "top3_day_exclusion_pnl_yen_100",
    "symbol_6976_exclusion_pnl_yen_100",
    "exception_rescue_top_symbol",
    "exception_rescue_top_share_pct",
]


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool_val(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _enrich_trades(reports: Path) -> list[dict[str, Any]]:
    merged = _merge_dataset(reports)
    p544 = {_trade_key(r): r for r in _load_csv(reports / "phase544_entry_feature_dataset.csv")}
    eng = {_trade_key(r): r for r in _load_csv(reports / "phase545c_engineered_features.csv")}
    out: list[dict[str, Any]] = []
    for row in merged:
        r = dict(row)
        for src in (p544.get(_trade_key(r)) or {}, eng.get(_trade_key(r)) or {}):
            for k, v in src.items():
                if k not in r or r.get(k) in (None, ""):
                    r[k] = v
        p544_row = p544.get(_trade_key(r)) or {}
        if r.get("entry_type") in (None, ""):
            r["entry_type"] = p544_row.get("entry_type")
        if r.get("pbv2_or") in (None, ""):
            r["pbv2_or"] = p544_row.get("pbv2_or")
        out.append(r)
    return out


def _reject_reason(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if _cluster_id_val(row) == 5:
        parts.append("cluster5")
    csub = _csub_id(row)
    if csub in (0, 2, 3, 5):
        parts.append(f"csub{csub}")
    return "|".join(parts)


def _open_strength_proxy(row: Mapping[str, Any]) -> bool:
    rank = _float(row.get("day_return_rank"))
    mins = _float(row.get("minutes_from_open"))
    rise5 = _float(row.get("entry_rise_5min_pct")) or _float(row.get("return_5min_pct"))
    if rise5 is not None and mins is not None:
        return mins <= 120.0 and rise5 > 0.2
    return rank is not None and mins is not None and mins <= 90.0 and rank <= 40.0


def _feature_vals(rows: Sequence[Mapping[str, Any]], feat: str, *, winner: bool) -> list[float]:
    out: list[float] = []
    for r in rows:
        win = _is_winner(r)
        if win != winner:
            continue
        if feat in ("high_update_recent", "prior_high_break"):
            out.append(1.0 if _bool_val(r.get(feat)) else 0.0)
            continue
        if feat == "entry_type":
            continue
        v = _float(r.get(feat))
        if v is not None:
            out.append(v)
    return out


def _population_rows(rejected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in rejected:
        rows.append(
            {
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "entry_time": t.get("entry_time"),
                "cluster_id": _cluster_id_val(t),
                "subcluster_id": _subcluster_id(t),
                "new_subcluster_id": _csub_id(t),
                "reject_reason": _reject_reason(t),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "mfe_pct": t.get("mfe_pct"),
                "is_loser": not _is_winner(t),
                "is_winner": _is_winner(t),
                "is_big_winner": _is_big_winner_row(t),
                "is_mfe0": _as_bool(t.get("is_mfe0")) or _is_mfe0(t),
                "is_stop_low_mfe": _as_bool(t.get("is_stop_low_mfe")) or _is_stop_low_mfe(t),
                "is_no_progress": _as_bool(t.get("is_no_progress")) or _is_no_progress(t),
                "entry_type": t.get("entry_type"),
                "pbv2_or": t.get("pbv2_or"),
                "board_imbalance": t.get("board_imbalance"),
                "volume_percentile": t.get("volume_percentile"),
                "relative_volume": t.get("relative_volume"),
                "day_return_rank": t.get("day_return_rank"),
            }
        )
    return rows


def _population_summary(rejected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sym = Counter(str(t.get("symbol") or "").replace(".T", "") for t in rejected)
    day = Counter(str(t.get("day") or "")[:8] for t in rejected)
    cluster = Counter(_reject_reason(t) for t in rejected)
    entry = Counter(str(t.get("entry_type") or "unknown") for t in rejected)
    pb = Counter(str(t.get("pbv2_or") or "unknown") for t in rejected)
    return {
        "total_rejected": len(rejected),
        "loser_count": sum(1 for t in rejected if not _is_winner(t)),
        "winner_count": sum(1 for t in rejected if _is_winner(t)),
        "big_winner_count": sum(1 for t in rejected if _is_big_winner_row(t)),
        "mfe0_count": sum(1 for t in rejected if _as_bool(t.get("is_mfe0")) or _is_mfe0(t)),
        "stop_low_mfe_count": sum(1 for t in rejected if _as_bool(t.get("is_stop_low_mfe")) or _is_stop_low_mfe(t)),
        "no_progress_count": sum(1 for t in rejected if _as_bool(t.get("is_no_progress")) or _is_no_progress(t)),
        "or_entry_count": sum(1 for t in rejected if str(t.get("entry_type") or "").upper() == "OR"),
        "pbv2_entry_count": sum(1 for t in rejected if str(t.get("pbv2_or") or "").upper() == "PBV2"),
        "top3_symbols": sym.most_common(3),
        "top3_days": day.most_common(3),
        "top_clusters": cluster.most_common(8),
        "entry_type_breakdown": dict(entry),
        "pbv2_or_breakdown": dict(pb),
    }


def _separation_rows(rejected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n_win = sum(1 for t in rejected if _is_winner(t))
    n_loss = sum(1 for t in rejected if not _is_winner(t))
    for feat in SEPARATION_FEATURES:
        if feat == "entry_type":
            continue
        wv = _feature_vals(rejected, feat, winner=True)
        lv = _feature_vals(rejected, feat, winner=False)
        d = _cohens_d(wv, lv) if len(wv) >= 3 and len(lv) >= 3 else None
        sep = _separation_score(wv, lv) if len(wv) >= 2 and len(lv) >= 2 else None
        out.append(
            {
                "feature": feat,
                "winner_count": len(wv),
                "loser_count": len(lv),
                "median_winner": round(statistics.median(wv), 6) if wv else None,
                "median_loser": round(statistics.median(lv), 6) if lv else None,
                "p25_winner": _percentile(wv, 25),
                "p75_winner": _percentile(wv, 75),
                "p25_loser": _percentile(lv, 25),
                "p75_loser": _percentile(lv, 75),
                "missing_rate_winner": round(1.0 - len(wv) / n_win, 4) if n_win else 0.0,
                "missing_rate_loser": round(1.0 - len(lv) / n_loss, 4) if n_loss else 0.0,
                "cohens_d": round(d, 4) if d is not None else None,
                "separation_score": round(sep, 4) if sep is not None else None,
            }
        )
    out.sort(key=lambda r: abs(_num(r.get("separation_score"))), reverse=True)
    return out


def _big_winner_rows(rejected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in rejected:
        if not _is_big_winner_row(t):
            continue
        signals: list[str] = []
        if (_float(t.get("board_imbalance")) or 0) >= 0.55:
            signals.append("board_strong")
        if (_float(t.get("volume_percentile")) or 0) >= 70:
            signals.append("volume_strong")
        if (_float(t.get("relative_volume")) or 0) >= 1.1:
            signals.append("rel_vol_strong")
        if _bool_val(t.get("high_update_recent")):
            signals.append("high_update")
        if (_float(t.get("day_return_rank")) or 999) <= 20:
            signals.append("day_leader")
        if _open_strength_proxy(t):
            signals.append("open_strength")
        if str(t.get("entry_type") or "").upper() == "OR":
            signals.append("or_entry")
        rows.append(
            {
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "entry_time": t.get("entry_time"),
                "cluster_id": _cluster_id_val(t),
                "new_subcluster_id": _csub_id(t),
                "reject_reason": _reject_reason(t),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "mfe_pct": t.get("mfe_pct"),
                "board_imbalance": t.get("board_imbalance"),
                "volume_percentile": t.get("volume_percentile"),
                "relative_volume": t.get("relative_volume"),
                "liquidity_burst": t.get("liquidity_burst"),
                "update_count_before_entry": t.get("update_count_before_entry"),
                "high_update_recent": t.get("high_update_recent"),
                "day_return_rank": t.get("day_return_rank"),
                "open_strength_proxy": _open_strength_proxy(t),
                "entry_type": t.get("entry_type"),
                "pbv2_or": t.get("pbv2_or"),
                "rescue_signal_summary": "|".join(signals),
            }
        )
    return rows


def _period_thresholds(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for feat in ("liquidity_burst", "price_acceleration"):
        vals = [_float(t.get(feat)) for t in trades]
        nums = [v for v in vals if v is not None]
        out[f"{feat}_p75"] = _percentile(nums, 75) or 0.0
    return out


def _build_exception_fns(thresholds: Mapping[str, float]) -> dict[str, tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    return {
        "E1": ("Board Strong", "board_imbalance >= 0.60", lambda r: (_float(r.get("board_imbalance")) or 0) >= 0.60),
        "E2": ("Volume Strong", "volume_percentile >= 80", lambda r: (_float(r.get("volume_percentile")) or 0) >= 80.0),
        "E3": ("Relative Volume Strong", "relative_volume >= 1.20", lambda r: (_float(r.get("relative_volume")) or 0) >= 1.20),
        "E4": (
            "Liquidity Burst",
            f"liquidity_burst >= p75 ({thresholds.get('liquidity_burst_p75')})",
            lambda r: (_float(r.get("liquidity_burst")) or 0) >= float(thresholds.get("liquidity_burst_p75") or 0),
        ),
        "E5": ("Day Leader", "day_return_rank <= 20", lambda r: (_float(r.get("day_return_rank")) or 999) <= 20.0),
        "E6": ("High Update", "high_update_recent == true", lambda r: _bool_val(r.get("high_update_recent"))),
        "E7": ("Prior High Break", "prior_high_break == true", lambda r: _bool_val(r.get("prior_high_break"))),
        "E8": (
            "Price Acceleration",
            f"price_acceleration >= p75 ({thresholds.get('price_acceleration_p75')})",
            lambda r: (_float(r.get("price_acceleration")) or -1e9) >= float(thresholds.get("price_acceleration_p75") or 0),
        ),
        "E9": ("OR Entry Rescue", "entry_type == OR", lambda r: str(r.get("entry_type") or "").upper() == "OR"),
        "E10": (
            "Board + Volume",
            "board_imbalance >= 0.55 AND volume_percentile >= 70",
            lambda r: (_float(r.get("board_imbalance")) or 0) >= 0.55 and (_float(r.get("volume_percentile")) or 0) >= 70.0,
        ),
        "E11": (
            "Relative Volume + Update",
            "relative_volume >= 1.10 AND high_update_recent",
            lambda r: (_float(r.get("relative_volume")) or 0) >= 1.10 and _bool_val(r.get("high_update_recent")),
        ),
        "E12": (
            "Day Leader + Volume",
            "day_return_rank <= 20 AND volume_percentile >= 70",
            lambda r: (_float(r.get("day_return_rank")) or 999) <= 20.0 and (_float(r.get("volume_percentile")) or 0) >= 70.0,
        ),
    }


def _evaluate_variant(
    trades: Sequence[Mapping[str, Any]],
    *,
    exception_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    baseline_pnl: float,
    v6_pnl: float,
    total_trades: int,
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    recovered: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        rejected = _is_rejected(row, V6_SPEC)
        if rejected and exception_fn and exception_fn(row):
            accepted.append(row)
            recovered.append(row)
        elif rejected:
            blocked.append(row)
        else:
            accepted.append(row)
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    rec_win = [t for t in recovered if _is_winner(t)]
    rec_big = [t for t in recovered if _is_big_winner_row(t)]
    reintro_losers = [t for t in recovered if not _is_winner(t)]
    reintro_mfe0 = [t for t in recovered if _as_bool(t.get("is_mfe0")) or _is_mfe0(t)]
    rec_win_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in rec_win), 2)
    reintro_loss = round(sum(_num(t.get("pnl_yen_100")) for t in reintro_losers), 2)
    n = len(accepted)
    return {
        "trades": n,
        "pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
        "mfe0_count": sum(1 for t in accepted if _as_bool(t.get("is_mfe0")) or _is_mfe0(t)),
        "stop_low_mfe_count": sum(1 for t in accepted if _as_bool(t.get("is_stop_low_mfe")) or _is_stop_low_mfe(t)),
        "no_progress_count": sum(1 for t in accepted if _as_bool(t.get("is_no_progress")) or _is_no_progress(t)),
        "big_winner_count": sum(1 for t in accepted if _is_big_winner_row(t)),
        "recovered_winner_count": len(rec_win),
        "recovered_big_winner_count": len(rec_big),
        "reintroduced_loser_count": len(reintro_losers),
        "reintroduced_mfe0_count": len(reintro_mfe0),
        "reintroduced_loss_pnl_yen_100": reintro_loss,
        "recovered_winner_pnl_yen_100": rec_win_pnl,
        "net_improvement_vs_baseline_yen_100": round(total - baseline_pnl, 2),
        "net_improvement_vs_v6_yen_100": round(total - v6_pnl, 2),
        "trade_retention_rate": round(n / total_trades, 4) if total_trades else 0.0,
        "_accepted": accepted,
        "_blocked": blocked,
        "_recovered": recovered,
    }


def _success_score(result: Mapping[str, Any], *, v6: Mapping[str, Any]) -> int:
    score = 0
    if _num(result.get("pnl_yen_100")) >= _num(v6.get("pnl_yen_100")):
        score += 1
    if _num(result.get("profit_factor")) >= _num(v6.get("profit_factor")) * 0.95:
        score += 1
    if _num(result.get("max_drawdown_yen_100")) <= _num(v6.get("max_drawdown_yen_100")) * 1.10:
        score += 1
    if int(result.get("recovered_big_winner_count") or 0) > 0:
        score += 1
    if int(result.get("reintroduced_mfe0_count") or 0) <= 30:
        score += 1
    rec_pnl = _num(result.get("recovered_winner_pnl_yen_100"))
    reintro = abs(_num(result.get("reintroduced_loss_pnl_yen_100")))
    if rec_pnl >= reintro:
        score += 1
    if _num(result.get("trade_retention_rate")) > _num(v6.get("trade_retention_rate")):
        score += 1
    return score


def _dependency_row(
    exception_id: str,
    result: Mapping[str, Any],
    *,
    v6_net: float,
    recovered: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    blocked = list(result.get("_blocked") or [])
    net = round(_num(result.get("net_improvement_vs_baseline_yen_100")), 2)
    sym_delta: dict[str, float] = defaultdict(float)
    day_delta: dict[str, float] = defaultdict(float)
    for t in blocked:
        pnl = _num(t.get("pnl_yen_100"))
        sym_delta[str(t.get("symbol") or "").replace(".T", "")] -= pnl
        day_delta[str(t.get("day") or "")[:8]] -= pnl
    sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
    day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
    top3_sym = round(sum(v for _, v in sym_sorted[:3]), 2)
    top3_day = round(sum(v for _, v in day_sorted[:3]), 2)
    top10 = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))[:10]
    sym6976 = sym_delta.get(SYMBOL_6976, 0.0)
    rescue_sym = Counter(str(t.get("symbol") or "").replace(".T", "") for t in recovered)
    top_rescue = rescue_sym.most_common(1)
    rescue_total = len(recovered) or 1
    return {
        "exception_id": exception_id,
        "top10_trade_exclusion_pnl_yen_100": round(net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2),
        "top3_symbol_exclusion_pnl_yen_100": round(net - top3_sym, 2),
        "top3_day_exclusion_pnl_yen_100": round(net - top3_day, 2),
        "symbol_6976_exclusion_pnl_yen_100": round(net - sym6976, 2),
        "exception_rescue_top_symbol": top_rescue[0][0] if top_rescue else "",
        "exception_rescue_top_share_pct": round(top_rescue[0][1] / rescue_total * 100.0, 2) if top_rescue else 0.0,
    }


def _mandatory_answers(
    rejected: Sequence[Mapping[str, Any]],
    separation: Sequence[Mapping[str, Any]],
    big_winner_rows: Sequence[Mapping[str, Any]],
    replay_rows: Sequence[Mapping[str, Any]],
    *,
    v6_result: Mapping[str, Any],
) -> dict[str, Any]:
    top_sep = [r for r in separation if r.get("cohens_d") is not None][:3]
    exc_rows = [r for r in replay_rows if r.get("exception_id") not in (None, "", "V6")]
    best = max(
        exc_rows,
        key=lambda r: (
            _num(r.get("net_improvement_vs_v6_yen_100")),
            int(r.get("success_score") or 0),
            int(r.get("recovered_big_winner_count") or 0),
        ),
        default={},
    )
    success_exc = [
        r
        for r in exc_rows
        if int(r.get("success_score") or 0) >= 6
        and int(r.get("recovered_big_winner_count") or 0) > 0
        and _num(r.get("pnl_yen_100")) >= _num(v6_result.get("pnl_yen_100"))
    ]
    bw_signals = Counter()
    for r in big_winner_rows:
        for s in str(r.get("rescue_signal_summary") or "").split("|"):
            if s:
                bw_signals[s] += 1
    return {
        "1_v6_reject_winner_count": sum(1 for t in rejected if _is_winner(t)),
        "2_v6_reject_big_winner_count": sum(1 for t in rejected if _is_big_winner_row(t)),
        "3_winner_loser_separator_features": [r.get("feature") for r in top_sep],
        "4_big_winner_common_signals": bw_signals.most_common(5),
        "5_rescue_exception_exists": len(success_exc) > 0,
        "6_best_exception": best.get("exception_id"),
        "7_pnl_improves_vs_v6": _num(best.get("pnl_yen_100")) > _num(v6_result.get("pnl_yen_100")),
        "8_pf_not_too_worse_vs_v6": _num(best.get("profit_factor")) >= _num(v6_result.get("profit_factor")) * 0.95,
        "9_mfe0_reintro_controlled": int(best.get("reintroduced_mfe0_count") or 0) <= 30,
        "10_retention_improves": _num(best.get("trade_retention_rate")) > _num(v6_result.get("trade_retention_rate")),
        "11_runtime_candidate_closer": False,
        "12_shadow_monitor_candidates": [r.get("exception_id") for r in success_exc],
        "13_next_phase": "phase548_entry_cluster_shadow_monitor",
    }


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    pop = result.get("population_summary") or {}
    sep = list(result.get("separation") or [])[:5]
    replay = list(result.get("replay_rows") or [])
    lines = [
        "# Phase547 — Reject Cluster Winner Rescue Analysis",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**V6 rejected trades:** {pop.get('total_rejected')}",
        "**Runtime変更:** なし / **採用:** なし",
        "",
        "## Reject population summary",
        "",
        f"- Winners: {pop.get('winner_count')} / Big Winners: {pop.get('big_winner_count')}",
        f"- Losers: {pop.get('loser_count')} / MFE0: {pop.get('mfe0_count')}",
        "",
        "## Top separation (Winner vs Loser within reject)",
        "",
    ]
    for s in sep:
        lines.append(
            f"- `{s.get('feature')}`: sep={s.get('separation_score')} d={s.get('cohens_d')} "
            f"med_w={s.get('median_winner')} med_l={s.get('median_loser')}"
        )
    lines.extend(["", "## V6 + Exception replay", ""])
    for r in replay:
        lines.append(
            f"- {r.get('exception_id')} {r.get('label')}: trades={r.get('trades')} PnL={r.get('pnl_yen_100')} "
            f"rec_big={r.get('recovered_big_winner_count')} score={r.get('success_score')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"


@dataclass
class Phase547Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        reports = resolve_reports_dir(repo)
        trades = _enrich_trades(reports)
        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in trades), 2)
        total_trades = len(trades)

        rejected = [dict(t) for t in trades if _is_rejected(t, V6_SPEC)]
        v6_result = _evaluate_variant(
            trades,
            exception_fn=None,
            baseline_pnl=baseline_pnl,
            v6_pnl=0.0,
            total_trades=total_trades,
        )
        v6_pnl = _num(v6_result.get("pnl_yen_100"))

        population = _population_rows(rejected)
        pop_summary = _population_summary(rejected)
        separation = _separation_rows(rejected)
        big_winners = _big_winner_rows(rejected)
        thresholds = _period_thresholds(trades)
        exception_defs = _build_exception_fns(thresholds)

        candidate_rows: list[dict[str, Any]] = []
        replay_rows: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []

        v6_replay = {
            "variant_id": "V6",
            "exception_id": "V6",
            "label": "Balanced Reject (no exception)",
            "trades": v6_result.get("trades"),
            "pnl_yen_100": v6_result.get("pnl_yen_100"),
            "profit_factor": v6_result.get("profit_factor"),
            "max_drawdown_yen_100": v6_result.get("max_drawdown_yen_100"),
            "win_rate": v6_result.get("win_rate"),
            "mfe0_count": v6_result.get("mfe0_count"),
            "stop_low_mfe_count": v6_result.get("stop_low_mfe_count"),
            "no_progress_count": v6_result.get("no_progress_count"),
            "big_winner_count": v6_result.get("big_winner_count"),
            "recovered_winner_count": 0,
            "recovered_big_winner_count": 0,
            "reintroduced_loser_count": 0,
            "reintroduced_mfe0_count": 0,
            "reintroduced_loss_pnl_yen_100": 0.0,
            "recovered_winner_pnl_yen_100": 0.0,
            "net_improvement_vs_baseline_yen_100": v6_result.get("net_improvement_vs_baseline_yen_100"),
            "net_improvement_vs_v6_yen_100": 0.0,
            "trade_retention_rate": v6_result.get("trade_retention_rate"),
            "success_score": 7,
            "runtime_candidate": False,
        }
        replay_rows.append(v6_replay)
        dependency_rows.append(
            _dependency_row("V6", v6_result, v6_net=v6_result.get("net_improvement_vs_baseline_yen_100"), recovered=[])
        )

        for eid, (label, rule, fn) in exception_defs.items():
            rescued = [t for t in rejected if fn(t)]
            candidate_rows.append(
                {
                    "exception_id": eid,
                    "label": label,
                    "rule": rule,
                    "threshold_note": rule,
                    "rescued_count": len(rescued),
                    "rescued_winner_count": sum(1 for t in rescued if _is_winner(t)),
                    "rescued_big_winner_count": sum(1 for t in rescued if _is_big_winner_row(t)),
                    "rescued_loser_count": sum(1 for t in rescued if not _is_winner(t)),
                    "rescued_mfe0_count": sum(1 for t in rescued if _as_bool(t.get("is_mfe0")) or _is_mfe0(t)),
                    "rescued_pnl_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in rescued), 2),
                    "rescued_loser_pnl_yen_100": round(
                        sum(_num(t.get("pnl_yen_100")) for t in rescued if not _is_winner(t)), 2
                    ),
                }
            )
            ev = _evaluate_variant(
                trades,
                exception_fn=fn,
                baseline_pnl=baseline_pnl,
                v6_pnl=v6_pnl,
                total_trades=total_trades,
            )
            row = {
                "variant_id": "V6",
                "exception_id": eid,
                "label": label,
                "trades": ev.get("trades"),
                "pnl_yen_100": ev.get("pnl_yen_100"),
                "profit_factor": ev.get("profit_factor"),
                "max_drawdown_yen_100": ev.get("max_drawdown_yen_100"),
                "win_rate": ev.get("win_rate"),
                "mfe0_count": ev.get("mfe0_count"),
                "stop_low_mfe_count": ev.get("stop_low_mfe_count"),
                "no_progress_count": ev.get("no_progress_count"),
                "big_winner_count": ev.get("big_winner_count"),
                "recovered_winner_count": ev.get("recovered_winner_count"),
                "recovered_big_winner_count": ev.get("recovered_big_winner_count"),
                "reintroduced_loser_count": ev.get("reintroduced_loser_count"),
                "reintroduced_mfe0_count": ev.get("reintroduced_mfe0_count"),
                "reintroduced_loss_pnl_yen_100": ev.get("reintroduced_loss_pnl_yen_100"),
                "recovered_winner_pnl_yen_100": ev.get("recovered_winner_pnl_yen_100"),
                "net_improvement_vs_baseline_yen_100": ev.get("net_improvement_vs_baseline_yen_100"),
                "net_improvement_vs_v6_yen_100": ev.get("net_improvement_vs_v6_yen_100"),
                "trade_retention_rate": ev.get("trade_retention_rate"),
                "success_score": _success_score(ev, v6=v6_result),
                "runtime_candidate": False,
            }
            replay_rows.append(row)
            dependency_rows.append(
                _dependency_row(
                    eid,
                    ev,
                    v6_net=_num(ev.get("net_improvement_vs_baseline_yen_100")),
                    recovered=list(ev.get("_recovered") or []),
                )
            )

        answers = _mandatory_answers(
            rejected,
            separation,
            big_winners,
            replay_rows,
            v6_result=v6_result,
        )

        return {
            "verdict": PHASE547_VERDICT,
            "generated_at": _now_iso(),
            "trade_count": total_trades,
            "v6_rejected_count": len(rejected),
            "baseline_pnl_yen_100": baseline_pnl,
            "v6_pnl_yen_100": v6_pnl,
            "v6_metrics": {k: v6_result.get(k) for k in (
                "trades", "pnl_yen_100", "profit_factor", "max_drawdown_yen_100", "mfe0_count",
                "big_winner_count", "trade_retention_rate",
            )},
            "population_summary": pop_summary,
            "population": population,
            "separation": separation,
            "big_winners": big_winners,
            "exception_candidates": candidate_rows,
            "replay_rows": replay_rows,
            "dependency_rows": dependency_rows,
            "thresholds": thresholds,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "population": reports / "phase547_reject_population.csv",
            "separation": reports / "phase547_reject_winner_loser_separation.csv",
            "big_winners": reports / "phase547_rejected_big_winners.csv",
            "candidates": reports / "phase547_exception_candidates.csv",
            "replay": reports / "phase547_v6_exception_replay.csv",
            "dependency": reports / "phase547_exception_dependency.csv",
            "report": reports / "phase547_report.json",
            "docs": kabu / "docs" / "operations" / "phase547_reject_cluster_winner_rescue.md",
        }
        _write_csv(paths["population"], POPULATION_FIELDS, list(result.get("population") or []))
        _write_csv(paths["separation"], SEPARATION_FIELDS, list(result.get("separation") or []))
        _write_csv(paths["big_winners"], BIG_WINNER_FIELDS, list(result.get("big_winners") or []))
        _write_csv(paths["candidates"], EXCEPTION_CANDIDATE_FIELDS, list(result.get("exception_candidates") or []))
        _write_csv(paths["replay"], REPLAY_FIELDS, list(result.get("replay_rows") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency_rows") or []))
        public = {
            k: v
            for k, v in result.items()
            if k not in ("population", "big_winners", "separation", "exception_candidates", "replay_rows", "dependency_rows")
        }
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths
