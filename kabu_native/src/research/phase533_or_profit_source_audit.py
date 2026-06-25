"""
Phase533 — OR Profit Source Audit (research only).

Decomposes O_R003_OR (S2_OR) profit sources: trade/symbol/day dependency,
B_or_only winner anatomy, clustering, and PBv2 comparison.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _momentum_score
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase507_classic_strategy_battle import _run_baseline_runtime, _universe_symbols
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase515b_day_high_breakout_dependency_audit import (
    SYMBOL_6976,
    _bar_index_at,
    _high_update_stats,
    _session_open_ts,
)
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase517_o_r003_or_robustness_audit import (
    _executed_trade_rows,
    _metrics_from_trades,
    _simulate_or_audited,
)
from research.phase518_day_high_winner_loser_separation import (
    _build_micro_lookup,
    _extract_entry_features,
    _percentile,
    _separation_score,
)
from research.phase522_stop_low_mfe_reentry_overlay_edge_audit import (
    _baseline_trade_rows,
    _day_return_rank,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase527_entry_quality_guard import _breakout_class
from research.phase530_winner_capture_research import _sym_key
from research.phase488_current_runtime_replay import _filter_period
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE533_VERDICT = "phase533_or_profit_source_audit_done"
MAX_WORKERS = 4
S2_OR = "S2_OR"

WINNER_FEATURES = (
    "rsi14",
    "adx14",
    "roc10",
    "vwap_distance",
    "spread_bps",
    "board_imbalance",
    "volume_percentile",
    "update_count",
    "day_high_update_speed",
    "minutes_from_open",
    "momentum_score",
)

TRADE_EXCLUSION_FIELDS = [
    "audit_type",
    "exclusion_type",
    "excluded_count",
    "excluded_pnl_yen_100",
    "excluded_pnl_share_pct",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remaining_max_dd_yen_100",
    "remaining_trades",
    "remains_positive",
]

SYMBOL_EXCLUSION_FIELDS = TRADE_EXCLUSION_FIELDS
DAY_EXCLUSION_FIELDS = [
    "audit_type",
    "exclusion_type",
    "excluded_count",
    "excluded_pnl_yen_100",
    "excluded_pnl_share_pct",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remaining_trades",
    "remains_positive",
]

CONTRIBUTION_FIELDS = [
    "rank_type",
    "rank",
    "key",
    "pnl_yen_100",
    "pnl_share_pct",
    "trade_count",
]

FEATURE_SEP_FIELDS = [
    "feature_id",
    "winner_count",
    "loser_count",
    "winner_median",
    "loser_median",
    "winner_p25",
    "winner_p75",
    "loser_p25",
    "loser_p75",
    "effect_size",
    "separation_score",
]

CLUSTER_FIELDS = [
    "cluster_id",
    "cluster_label",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "avg_mfe_pct",
    "avg_pnl_yen_100",
    "symbols_sample",
]

COMPARE_FIELDS = [
    "cohort",
    "trade_count",
    "avg_mfe_pct",
    "avg_mae_pct",
    "avg_hold_minutes",
    "avg_minutes_from_open",
    "median_entry_minutes_from_open",
    "avg_pnl_yen_100",
]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("exit_time") or t.get("entry_time") or ""))
        or datetime.min.replace(tzinfo=JST),
    )
    return [_num(t.get("pnl_yen_100")) for t in ordered]


def _hold_minutes(trade: Mapping[str, Any]) -> Optional[float]:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    ex = _parse_ts(str(trade.get("exit_time") or ""))
    if ent and ex:
        return round((ex - ent).total_seconds() / 60.0, 2)
    return None


def _b_or_only_keys(
    *,
    days: Sequence[str],
    price_idx: Mapping,
    universe: Sequence[str],
    baseline_trades: Sequence[Mapping[str, Any]],
    or_trades: Sequence[Mapping[str, Any]],
    top_n: int = 20,
) -> set[tuple[str, str]]:
    b_sd = {(_sym_key(t.get("symbol")), str(t.get("day") or "")[:8]) for t in baseline_trades}
    o_sd = {(_sym_key(t.get("symbol")), str(t.get("day") or "")[:8]) for t in or_trades}
    keys: set[tuple[str, str]] = set()
    for day in days:
        ranked = _day_return_rank(price_idx, universe, day)
        for sym, _ in ranked[:top_n]:
            key = (sym, day)
            if key not in b_sd and key in o_sd:
                keys.add(key)
    return keys


def _full_feature_row(
    trade: Mapping[str, Any],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
    trade_by_key: Mapping[str, Mapping[str, Any]],
    price_idx: Mapping,
    rank_map: Optional[Mapping[str, int]] = None,
) -> dict[str, Any]:
    pk = _position_key(trade)
    src = trade_by_key.get(pk, trade)
    feats = _extract_entry_features(src, bar_cache=bar_cache, micro_lookup=micro_lookup)
    sym = _sym_key(trade.get("symbol"))
    day = str(trade.get("day") or "")[:8]
    mins = feats.get("minutes_from_open")
    updates = _num(feats.get("update_count_before_entry"))
    speed = round(updates / max(_num(mins), 1.0), 6) if mins is not None else None

    sym_t = f"{sym}.T"
    roc10 = None
    cached = bar_cache.get((sym_t, day))
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    if cached and ent:
        bars, ind_rows = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            roc10 = ind_rows[ei].values.get("ROC10")

    if feats.get("day_high_update_count_before_entry") is None and cached and ent:
        bars, _ = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            stats = _high_update_stats(bars, ei, ei)
            feats = {**feats, **stats}

    mfe = trade.get("mfe_pct")
    mae = trade.get("mae_pct")
    if mfe is None:
        mfe, mae = _mfe_mae_to_exit(src, price_idx=price_idx, exit_ts_iso=str(trade.get("exit_time") or ""))

    vwap = feats.get("vwap_distance_pct")
    if vwap is None:
        vwap = feats.get("price_vs_vwap")

    return {
        "symbol": sym,
        "day": day,
        "position_key": pk,
        "entry_time": trade.get("entry_time"),
        "pnl_yen_100": trade.get("pnl_yen_100"),
        "mfe_pct": mfe,
        "mae_pct": mae,
        "day_return_rank": rank_map.get(sym) if rank_map else None,
        "rsi14": feats.get("rsi14"),
        "adx14": feats.get("adx14"),
        "roc10": roc10,
        "vwap_distance": vwap,
        "spread_bps": feats.get("spread"),
        "board_imbalance": feats.get("board_imbalance"),
        "volume_percentile": feats.get("rolling_volume_percentile"),
        "update_count": feats.get("update_count_before_entry"),
        "day_high_update_speed": speed,
        "minutes_from_open": mins,
        "momentum_score": _momentum_score(src),
        "breakout_type": feats.get("breakout_type"),
        "outcome": "winner" if _num(trade.get("pnl_yen_100")) > 0 else "loser",
    }


def _contribution_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    rank_type: str,
    key_fn,
) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for t in trades:
        k = key_fn(t)
        buckets[k] += _num(t.get("pnl_yen_100"))
        counts[k] += 1
    total = sum(buckets.values())
    ranked = sorted(buckets.items(), key=lambda x: x[1], reverse=True)
    rows: list[dict[str, Any]] = []
    for i, (k, pnl) in enumerate(ranked, start=1):
        rows.append(
            {
                "rank_type": rank_type,
                "rank": i,
                "key": k,
                "pnl_yen_100": round(pnl, 2),
                "pnl_share_pct": round(pnl / total * 100.0, 2) if total else 0.0,
                "trade_count": counts[k],
            }
        )
    return rows


def _exclusion_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    audit_type: str,
    group: str,
    top_ns: Sequence[int],
    key_fn,
    fields: Sequence[str],
) -> list[dict[str, Any]]:
    total_pnl = sum(_num(t.get("pnl_yen_100")) for t in trades)
    if group == "trade":
        ranked_trades = sorted(trades, key=lambda x: _num(x.get("pnl_yen_100")), reverse=True)
        keys_by_n = {n: {_position_key(t) for t in ranked_trades[:n]} for n in top_ns}
    else:
        bucket_pnl: dict[str, float] = defaultdict(float)
        for t in trades:
            bucket_pnl[key_fn(t)] += _num(t.get("pnl_yen_100"))
        ranked_keys = [k for k, _ in sorted(bucket_pnl.items(), key=lambda x: x[1], reverse=True)]
        keys_by_n = {n: set(ranked_keys[:n]) for n in top_ns}

    rows: list[dict[str, Any]] = []
    for n in top_ns:
        ex_keys = keys_by_n[n]
        if group == "trade":
            excluded = [t for t in trades if _position_key(t) in ex_keys]
            remaining = [t for t in trades if _position_key(t) not in ex_keys]
        elif group == "symbol":
            excluded = [t for t in trades if key_fn(t) in ex_keys]
            remaining = [t for t in trades if key_fn(t) not in ex_keys]
        else:
            excluded = [t for t in trades if key_fn(t) in ex_keys]
            remaining = [t for t in trades if key_fn(t) not in ex_keys]

        ex_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in excluded), 2)
        rem_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in remaining), 2)
        rem_pnls = _chron_pnls(remaining)
        ex_label = f"top{n}_{group}" + ("s" if n > 1 else "")
        row = {
            "audit_type": audit_type,
            "exclusion_type": ex_label,
            "excluded_count": len(ex_keys),
            "excluded_pnl_yen_100": ex_pnl,
            "excluded_pnl_share_pct": round(ex_pnl / total_pnl * 100.0, 2) if total_pnl else 0.0,
            "remaining_pnl_yen_100": rem_pnl,
            "remaining_pf": _pf(rem_pnls),
            "remaining_trades": len(remaining),
            "remains_positive": rem_pnl > 0,
        }
        if "remaining_max_dd_yen_100" in fields:
            row["remaining_max_dd_yen_100"] = round(_max_drawdown_yen(rem_pnls) if rem_pnls else 0.0, 2)
        rows.append(row)

    sym6976_pnl = sum(_num(t.get("pnl_yen_100")) for t in trades if _sym_key(t.get("symbol")) == SYMBOL_6976)
    rem6976 = [t for t in trades if _sym_key(t.get("symbol")) != SYMBOL_6976]
    rem6976_pnls = _chron_pnls(rem6976)
    rem6976_pnl = round(sum(rem6976_pnls), 2)
    rows.append(
        {
            "audit_type": audit_type,
            "exclusion_type": f"symbol_{SYMBOL_6976}",
            "excluded_count": 1,
            "excluded_pnl_yen_100": round(sym6976_pnl, 2),
            "excluded_pnl_share_pct": round(sym6976_pnl / total_pnl * 100.0, 2) if total_pnl else 0.0,
            "remaining_pnl_yen_100": rem6976_pnl,
            "remaining_pf": _pf(rem6976_pnls),
            "remaining_max_dd_yen_100": round(_max_drawdown_yen(rem6976_pnls) if rem6976_pnls else 0.0, 2),
            "remaining_trades": len(rem6976),
            "remains_positive": rem6976_pnl > 0,
        }
    )
    return rows


def _feature_separation_rows(
    winners: Sequence[Mapping[str, Any]],
    losers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in WINNER_FEATURES:
        wv = [_num(r.get(feat)) for r in winners if r.get(feat) is not None]
        lv = [_num(r.get(feat)) for r in losers if r.get(feat) is not None]
        if len(wv) < 2 or len(lv) < 2:
            rows.append(
                {
                    "feature_id": feat,
                    "winner_count": len(wv),
                    "loser_count": len(lv),
                    "winner_median": round(statistics.median(wv), 6) if wv else None,
                    "loser_median": round(statistics.median(lv), 6) if lv else None,
                    "winner_p25": _percentile(wv, 25) if wv else None,
                    "winner_p75": _percentile(wv, 75) if wv else None,
                    "loser_p25": _percentile(lv, 25) if lv else None,
                    "loser_p75": _percentile(lv, 75) if lv else None,
                    "effect_size": None,
                    "separation_score": None,
                }
            )
            continue
        rows.append(
            {
                "feature_id": feat,
                "winner_count": len(wv),
                "loser_count": len(lv),
                "winner_median": round(statistics.median(wv), 6),
                "loser_median": round(statistics.median(lv), 6),
                "winner_p25": _percentile(wv, 25),
                "winner_p75": _percentile(wv, 75),
                "loser_p25": _percentile(lv, 25),
                "loser_p75": _percentile(lv, 75),
                "effect_size": _cohens_d(wv, lv),
                "separation_score": _separation_score(wv, lv),
            }
        )
    return rows


def _assign_cluster(row: Mapping[str, Any]) -> tuple[str, str]:
    mins = _num(row.get("minutes_from_open"))
    updates = _num(row.get("update_count"))
    vol = _num(row.get("volume_percentile"))
    rank = row.get("day_return_rank")
    speed = _num(row.get("day_high_update_speed"))
    breakout = str(row.get("breakout_type") or "")

    if mins <= 60 and updates <= 3 and breakout in ("true_breakout", "noise", ""):
        return "A", "early_breakout"
    if rank is not None and int(rank) <= 10 and mins <= 90:
        return "B", "open_strength"
    if vol >= 80:
        return "C", "volume_surge"
    if updates >= 4 or speed >= 0.03:
        return "D", "high_update_continuation"
    return "E", "other"


def _cluster_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    labels = {
        "A": "early_breakout",
        "B": "open_strength",
        "C": "volume_surge",
        "D": "high_update_continuation",
        "E": "other",
    }
    for r in enriched:
        cid, _ = _assign_cluster(r)
        buckets[cid].append(dict(r))

    rows: list[dict[str, Any]] = []
    for cid in ("A", "B", "C", "D", "E"):
        items = buckets.get(cid, [])
        if not items:
            continue
        pnls = [_num(t.get("pnl_yen_100")) for t in items]
        mfes = [_num(t.get("mfe_pct")) for t in items if t.get("mfe_pct") is not None]
        rows.append(
            {
                "cluster_id": cid,
                "cluster_label": labels[cid],
                "trade_count": len(items),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else None,
                "avg_pnl_yen_100": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
                "symbols_sample": ",".join(sorted({_sym_key(t.get("symbol")) for t in items})[:5]),
            }
        )
    return rows


def _cohort_compare_row(cohort: str, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mfes = [_num(t.get("mfe_pct")) for t in trades if t.get("mfe_pct") is not None]
    maes = [_num(t.get("mae_pct")) for t in trades if t.get("mae_pct") is not None]
    holds = [_num(t.get("hold_minutes")) for t in trades if t.get("hold_minutes") is not None]
    mins = [_num(t.get("minutes_from_open")) for t in trades if t.get("minutes_from_open") is not None]
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    return {
        "cohort": cohort,
        "trade_count": len(trades),
        "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else None,
        "avg_mae_pct": round(statistics.mean(maes), 4) if maes else None,
        "avg_hold_minutes": round(statistics.mean(holds), 2) if holds else None,
        "avg_minutes_from_open": round(statistics.mean(mins), 2) if mins else None,
        "median_entry_minutes_from_open": round(statistics.median(mins), 2) if mins else None,
        "avg_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
    }


def _load_or_and_baseline(
    repo_root: Path,
    *,
    price_idx: Mapping,
    period_end: str,
    parallel: bool,
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], Mapping, Mapping, Mapping[str, Mapping[str, Any]]]:
    bar_cache, days = _build_bar_cache(repo_root)
    replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(repo_root)
    universe = _universe_symbols(_filter_period(replay_pool, start=PERIOD_START, end=period_end))
    micro_lookup = _build_micro_lookup(replay_pool)
    trade_by_key = {_position_key(t): t for t in replay_pool}

    pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
    overlay_def = OVERLAY_DEFS["O_R003"]

    def _scan_day(day: str) -> list[dict[str, Any]]:
        return _scan_overlay_day(
            overlay_def, day=day, universe=universe, bar_cache=bar_cache, price_idx=price_idx
        )

    overlay_by_day: dict[str, list[dict[str, Any]]] = {}
    if parallel:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_scan_day, day): day for day in days}
            for fut in as_completed(futs):
                overlay_by_day[futs[fut]] = fut.result()
    else:
        for day in days:
            overlay_by_day[day] = _scan_day(day)

    overlay_all = [t for chunk in overlay_by_day.values() for t in chunk]
    merged = _merge_or_candidates(
        pbv2_candidates, overlay_all, bar_cache=bar_cache, overlay=overlay_def, guard_c_block=guard_c_block
    )
    or_result = _simulate_or_audited(merged, mode="phase533_s2_or")
    or_raw = _executed_trade_rows(or_result.state, S2_OR)

    baseline_state, _ = _run_baseline_runtime(repo_root)
    baseline_raw = _baseline_trade_rows(baseline_state, trade_by_key, price_idx)

    def _enrich(raw_rows: Sequence[Mapping[str, Any]], sid: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in raw_rows:
            pk = str(r.get("position_key") or _position_key(r))
            src = trade_by_key.get(pk, {})
            mfe = r.get("mfe_pct")
            mae = r.get("mae_pct")
            if mfe is None or mfe == "":
                mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=str(r.get("exit_time") or ""))
            out.append(
                {
                    **dict(r),
                    "strategy_id": sid,
                    "position_key": pk,
                    "mfe_pct": mfe,
                    "mae_pct": mae,
                    "hold_minutes": _hold_minutes(r),
                    "breakout_class": _breakout_class({**dict(r), "mfe_pct": mfe, "mae_pct": mae}, bar_cache),
                }
            )
        return out

    or_trades = _enrich(or_raw, S2_OR)
    baseline_trades = _enrich(
        [{**dict(r), "accepted_by_pbv2": True, "accepted_by_overlay": False} for r in baseline_raw],
        "BASELINE",
    )
    period_end = _latest_live_day(repo_root)
    days_f = [d for d in days if d >= PERIOD_START and d <= period_end]
    return or_trades, baseline_trades, days_f, bar_cache, micro_lookup, trade_by_key


def _mandatory_answers(
    *,
    trade_excl: Sequence[Mapping[str, Any]],
    symbol_excl: Sequence[Mapping[str, Any]],
    day_excl: Sequence[Mapping[str, Any]],
    feature_sep: Sequence[Mapping[str, Any]],
    clusters: Sequence[Mapping[str, Any]],
    compare_rows: Sequence[Mapping[str, Any]],
    enriched_72: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def _rem(audit: Sequence[Mapping[str, Any]], ex_type: str) -> dict[str, Any]:
        return next((r for r in audit if r.get("exclusion_type") == ex_type), {})

    top1_trade = _rem(trade_excl, "top1_trade")
    top10_trade = _rem(trade_excl, "top10_trades")
    top3_sym = _rem(symbol_excl, "top3_symbols")
    top10_sym = _rem(symbol_excl, "top10_symbols")
    sym6976 = _rem(symbol_excl, f"symbol_{SYMBOL_6976}")
    top3_day = _rem(day_excl, "top3_days")

    top1_share = _float(top1_trade.get("excluded_pnl_share_pct"))
    top10_share = _float(top10_trade.get("excluded_pnl_share_pct"))
    top3_sym_share = _float(top3_sym.get("excluded_pnl_share_pct"))
    top3_day_share = _float(top3_day.get("excluded_pnl_share_pct"))

    sep_ranked = sorted(
        [r for r in feature_sep if r.get("effect_size") is not None],
        key=lambda r: abs(_float(r.get("effect_size"))),
        reverse=True,
    )
    best_feat = sep_ranked[0] if sep_ranked else {}

    dominant_cluster = max(clusters, key=lambda r: _float(r.get("total_pnl_yen_100")), default={})

    or_w = next((r for r in compare_rows if r.get("cohort") == "OR_winner"), {})
    pb_w = next((r for r in compare_rows if r.get("cohort") == "PBv2_winner"), {})

    winner_medians = {r["feature_id"]: r.get("winner_median") for r in feature_sep if r.get("winner_median") is not None}
    loser_medians = {r["feature_id"]: r.get("loser_median") for r in feature_sep if r.get("loser_median") is not None}

    absorbable = []
    if _num(winner_medians.get("update_count")) > _num(loser_medians.get("update_count")):
        absorbable.append("update_count")
    if _num(winner_medians.get("minutes_from_open")) < _num(loser_medians.get("minutes_from_open")):
        absorbable.append("early_timing")
    if _num(winner_medians.get("volume_percentile")) > _num(loser_medians.get("volume_percentile")):
        absorbable.append("volume_percentile")

    runtime_close = (
        _num(top3_sym.get("remaining_pnl_yen_100")) > 0
        and _float(top10_trade.get("remaining_pnl_yen_100")) < 0
        and len(enriched_72) >= 50
    )

    return {
        "1_profit_concentrated_few_trades": top10_share >= 40.0 or top1_share >= 20.0,
        "1_top10_trade_pnl_share_pct": top10_share,
        "2_profit_concentrated_few_symbols": top3_sym_share >= 50.0,
        "2_top3_symbol_pnl_share_pct": top3_sym_share,
        "3_profit_concentrated_few_days": top3_day_share >= 50.0,
        "3_top3_day_pnl_share_pct": top3_day_share,
        "4_survives_top3_symbol_exclusion": _num(top3_sym.get("remaining_pnl_yen_100")) > 0,
        "4_top3_symbol_remaining_pnl": top3_sym.get("remaining_pnl_yen_100"),
        "5_survives_top10_trade_exclusion": _num(top10_trade.get("remaining_pnl_yen_100")) > 0,
        "5_top10_trade_remaining_pnl": top10_trade.get("remaining_pnl_yen_100"),
        "6_or_6976_dependent": _float(sym6976.get("excluded_pnl_share_pct")) >= 30.0,
        "6_symbol_6976_pnl_share_pct": sym6976.get("excluded_pnl_share_pct"),
        "6_6976_exclusion_remaining_pnl": sym6976.get("remaining_pnl_yen_100"),
        "7_b_or_only_common_features": list(absorbable),
        "8_best_separating_feature": best_feat.get("feature_id"),
        "8_best_effect_size": best_feat.get("effect_size"),
        "9_or_strategy_captures": dominant_cluster.get("cluster_label"),
        "10_pbv2_vs_or_essential_diff": (
            f"OR earlier entry ({or_w.get('avg_minutes_from_open')} vs {pb_w.get('avg_minutes_from_open')} min), "
            f"higher MFE ({or_w.get('avg_mfe_pct')} vs {pb_w.get('avg_mfe_pct')}%)"
        ),
        "11_next_research_candidate": "OR_winner_cluster_filter" if dominant_cluster else "OR_profit_deconcentration",
        "12_runtime_candidate_closer": runtime_close,
        "winner_count_72": sum(1 for r in enriched_72 if r.get("outcome") == "winner"),
        "loser_count_72": sum(1 for r in enriched_72 if r.get("outcome") == "loser"),
    }


def _render_doc(result: Mapping[str, Any]) -> str:
    ans = result.get("mandatory_answers") or {}
    lines = [
        "# Phase533 — OR Profit Source Audit",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in sorted(ans.items()):
        lines.append(f"- **{k}:** {v}")
    lines.append("\nResearch only — no Runtime adoption.\n")
    return "\n".join(lines)


@dataclass
class Phase533Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        period_end = _latest_live_day(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=period_end)

        or_trades, baseline_trades, days, bar_cache, micro_lookup, trade_by_key = _load_or_and_baseline(
            self.repo_root, price_idx=price_idx, period_end=period_end, parallel=self.parallel, workers=workers
        )
        universe = _universe_symbols(
            _filter_period(
                list(trade_by_key.values()),
                start=PERIOD_START,
                end=period_end,
            )
        )

        b_or_keys = _b_or_only_keys(
            days=days,
            price_idx=price_idx,
            universe=universe,
            baseline_trades=baseline_trades,
            or_trades=or_trades,
        )

        enrich_jobs = [(day, "b_or") for day in days] + [(day, "compare") for day in days]

        def _enrich_day(day: str, cohort: str) -> list[dict[str, Any]]:
            ranked = _day_return_rank(price_idx, universe, day)
            rank_map = {sym: i + 1 for i, (sym, _) in enumerate(ranked)}
            rows: list[dict[str, Any]] = []
            if cohort == "b_or":
                for t in or_trades:
                    if str(t.get("day") or "")[:8] != day:
                        continue
                    key = (_sym_key(t.get("symbol")), day)
                    if key not in b_or_keys or not t.get("accepted_by_overlay"):
                        continue
                    row = _full_feature_row(
                        t,
                        bar_cache=bar_cache,
                        micro_lookup=micro_lookup,
                        trade_by_key=trade_by_key,
                        price_idx=price_idx,
                        rank_map=rank_map,
                    )
                    row["hold_minutes"] = _hold_minutes(t)
                    rows.append(row)
            else:
                for t, label in (
                    *[(x, "OR_winner") for x in or_trades if x.get("accepted_by_overlay")],
                    *[(x, "PBv2_winner") for x in baseline_trades],
                ):
                    if str(t.get("day") or "")[:8] != day:
                        continue
                    if _num(t.get("pnl_yen_100")) <= 0:
                        continue
                    row = _full_feature_row(
                        t,
                        bar_cache=bar_cache,
                        micro_lookup=micro_lookup,
                        trade_by_key=trade_by_key,
                        price_idx=price_idx,
                        rank_map=rank_map,
                    )
                    row["hold_minutes"] = _hold_minutes(t)
                    row["cohort"] = label
                    rows.append(row)
            return rows

        enriched_b_or: list[dict[str, Any]] = []
        compare_pool: list[dict[str, Any]] = []

        if self.parallel and enrich_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_enrich_day, day, cohort): (day, cohort) for day, cohort in enrich_jobs}
                for fut in as_completed(futs):
                    day, cohort = futs[fut]
                    chunk = fut.result()
                    if cohort == "b_or":
                        enriched_b_or.extend(chunk)
                    else:
                        compare_pool.extend(chunk)
        else:
            for day, cohort in enrich_jobs:
                chunk = _enrich_day(day, cohort)
                if cohort == "b_or":
                    enriched_b_or.extend(chunk)
                else:
                    compare_pool.extend(chunk)

        winners_72 = [r for r in enriched_b_or if r.get("outcome") == "winner"]
        losers_72 = [r for r in enriched_b_or if r.get("outcome") == "loser"]
        feature_sep = _feature_separation_rows(winners_72, losers_72)
        clusters = _cluster_rows(enriched_b_or)

        trade_contrib = _contribution_rows(or_trades, rank_type="trade", key_fn=lambda t: _position_key(t))
        symbol_contrib = _contribution_rows(or_trades, rank_type="symbol", key_fn=lambda t: _sym_key(t.get("symbol")))
        day_contrib = _contribution_rows(or_trades, rank_type="day", key_fn=lambda t: str(t.get("day") or "")[:8])

        trade_excl = _exclusion_rows(
            or_trades,
            audit_type="trade",
            group="trade",
            top_ns=(1, 3, 5, 10, 20),
            key_fn=lambda t: _position_key(t),
            fields=TRADE_EXCLUSION_FIELDS,
        )
        symbol_excl = _exclusion_rows(
            or_trades,
            audit_type="symbol",
            group="symbol",
            top_ns=(1, 3, 5, 10),
            key_fn=lambda t: _sym_key(t.get("symbol")),
            fields=SYMBOL_EXCLUSION_FIELDS,
        )
        day_excl = _exclusion_rows(
            or_trades,
            audit_type="day",
            group="day",
            top_ns=(1, 3, 5),
            key_fn=lambda t: str(t.get("day") or "")[:8],
            fields=DAY_EXCLUSION_FIELDS,
        )

        or_winners = [r for r in compare_pool if r.get("cohort") == "OR_winner"]
        pbv2_winners = [r for r in compare_pool if r.get("cohort") == "PBv2_winner"]
        compare_rows = [
            _cohort_compare_row("OR_winner", or_winners),
            _cohort_compare_row("PBv2_winner", pbv2_winners),
        ]

        same_symday: list[dict[str, Any]] = []
        or_by_sd = defaultdict(list)
        pb_by_sd = defaultdict(list)
        for t in or_trades:
            if t.get("accepted_by_overlay") and _num(t.get("pnl_yen_100")) > 0:
                or_by_sd[(_sym_key(t.get("symbol")), str(t.get("day") or "")[:8])].append(t)
        for t in baseline_trades:
            if _num(t.get("pnl_yen_100")) > 0:
                pb_by_sd[(_sym_key(t.get("symbol")), str(t.get("day") or "")[:8])].append(t)
        for key in set(or_by_sd) & set(pb_by_sd):
            ot = min(or_by_sd[key], key=lambda x: str(x.get("entry_time") or ""))
            pt = min(pb_by_sd[key], key=lambda x: str(x.get("entry_time") or ""))
            ot_ent = _parse_ts(str(ot.get("entry_time") or ""))
            pt_ent = _parse_ts(str(pt.get("entry_time") or ""))
            if ot_ent and pt_ent:
                same_symday.append(
                    {
                        "symbol": key[0],
                        "day": key[1],
                        "or_earlier": ot_ent < pt_ent,
                        "or_mfe": ot.get("mfe_pct"),
                        "pb_mfe": pt.get("mfe_pct"),
                    }
                )

        mandatory = _mandatory_answers(
            trade_excl=trade_excl,
            symbol_excl=symbol_excl,
            day_excl=day_excl,
            feature_sep=feature_sep,
            clusters=clusters,
            compare_rows=compare_rows,
            enriched_72=enriched_b_or,
        )
        mandatory["overlap_symday_or_earlier_pct"] = (
            round(sum(1 for r in same_symday if r.get("or_earlier")) / len(same_symday), 4) if same_symday else None
        )
        mandatory["overlap_symday_or_higher_mfe_pct"] = (
            round(sum(1 for r in same_symday if _num(r.get("or_mfe")) > _num(r.get("pb_mfe"))) / len(same_symday), 4)
            if same_symday
            else None
        )

        return {
            "verdict": PHASE533_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": period_end,
            "includes_20260624": "20260624" in days,
            "parallel_workers": workers,
            "or_trade_count": len(or_trades),
            "b_or_only_count": len(b_or_keys),
            "b_or_enriched_count": len(enriched_b_or),
            "trade_exclusion": trade_excl,
            "symbol_exclusion": symbol_excl,
            "day_exclusion": day_excl,
            "profit_contribution": trade_contrib[:20] + symbol_contrib[:20] + day_contrib[:20],
            "winner_feature_separation": feature_sep,
            "winner_clusters": clusters,
            "pbv2_or_compare": compare_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "trade_excl": reports / "phase533_or_trade_exclusion.csv",
            "symbol_excl": reports / "phase533_or_symbol_exclusion.csv",
            "day_excl": reports / "phase533_or_day_exclusion.csv",
            "contribution": reports / "phase533_or_profit_contribution.csv",
            "features": reports / "phase533_or_winner_feature_separation.csv",
            "clusters": reports / "phase533_or_winner_clusters.csv",
            "compare": reports / "phase533_pbv2_or_winner_compare.csv",
            "report": reports / "phase533_report.json",
            "docs": kabu / "docs" / "operations" / "phase533_or_profit_source_audit.md",
        }
        _write_csv(paths["trade_excl"], TRADE_EXCLUSION_FIELDS, list(result.get("trade_exclusion") or []))
        _write_csv(paths["symbol_excl"], SYMBOL_EXCLUSION_FIELDS, list(result.get("symbol_exclusion") or []))
        _write_csv(paths["day_excl"], DAY_EXCLUSION_FIELDS, list(result.get("day_exclusion") or []))
        _write_csv(paths["contribution"], CONTRIBUTION_FIELDS, list(result.get("profit_contribution") or []))
        _write_csv(paths["features"], FEATURE_SEP_FIELDS, list(result.get("winner_feature_separation") or []))
        _write_csv(paths["clusters"], CLUSTER_FIELDS, list(result.get("winner_clusters") or []))
        _write_csv(paths["compare"], COMPARE_FIELDS, list(result.get("pbv2_or_compare") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["docs"].parent.mkdir(parents=True, exist_ok=True)
        paths["docs"].write_text(_render_doc(result), encoding="utf-8")
        return paths
