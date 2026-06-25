"""
Phase531 — O_R003_OR missed winner filter study (research only).

Separates PBv2-missed winners from O_R003_OR noise entries using entry-time features.
Tests G9 and filter candidates F1–F10. No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at, _high_update_stats
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
    _trade_rows_from_state,
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
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase527_entry_quality_guard import _guard_allows_entry
from research.phase530_winner_capture_research import (
    _avg_capture,
    _run_capture_day_job,
    _sym_key,
    _winner_capture_score,
)
from research.phase488_current_runtime_replay import _filter_period
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE531_VERDICT = "phase531_o_r003_or_missed_winner_filter_study_done"
MAX_WORKERS = 4

OR_STRATEGY = "O_R003_OR"
OR_G9_STRATEGY = "O_R003_OR_G9"
STRATEGIES = (BASELINE_STRATEGY_ID, OR_STRATEGY, OR_G9_STRATEGY)

FILTER_IDS = (
    "F1_spread50",
    "F2_update5",
    "F3_volpct80",
    "F4_adx30",
    "F5_rsi50",
    "F6_mins150",
    "F7_spread50_update5",
    "F8_spread50_volpct80",
    "F9_update5_volpct80",
    "F10_spread50_update5_volpct80",
)

NOISE_COMPARE_FEATURES = (
    "spread_bps",
    "update_count",
    "volume_percentile",
    "adx14",
    "rsi14",
    "vwap_distance",
    "minutes_from_open",
    "day_high_update_speed",
    "rolling_range_pct",
)

MISSED_WINNER_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "entry_price",
    "mfe_pct",
    "day_return_rank",
    "day_high_update_count",
    "spread_bps",
    "update_count",
    "volume_percentile",
    "adx14",
    "rsi14",
    "vwap_distance",
    "minutes_from_open",
]

NOISE_FEATURE_FIELDS = [
    "cohort",
    "feature_id",
    "count",
    "median",
    "p25",
    "p75",
    "effect_size",
    "separation_score",
]

OR_G9_CAPTURE_FIELDS = [
    "strategy_id",
    "day_return_top10_capture_rate",
    "day_return_top20_capture_rate",
    "mfe_gt1_capture_rate",
    "mfe_gt3_capture_rate",
    "pbv2_missed_winner_capture_count",
    "noise_entry_count",
    "stop_low_mfe_count",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "win_rate",
    "avg_pnl_yen_100",
    "winner_capture_score",
]

FILTER_SUMMARY_FIELDS = [
    "filter_id",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "win_rate",
    "avg_pnl_yen_100",
    "winner_capture_score",
    "pbv2_missed_winner_capture_count",
    "unique_winner_retention_rate",
    "noise_entry_reduction",
    "stop_low_mfe_reduction",
    "lost_winner_count",
    "prevented_noise_count",
    "net_improvement",
]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _passes_g9(feats: Mapping[str, Any]) -> bool:
    return _guard_allows_entry(
        "G9_spread50_update5",
        {
            "spread": feats.get("spread"),
            "update_count_before_entry": feats.get("update_count_before_entry"),
        },
    )


def _feature_map(feats: Mapping[str, Any]) -> dict[str, Any]:
    mins = _num(feats.get("minutes_from_open"))
    updates = _num(feats.get("update_count_before_entry"))
    speed = round(updates / max(mins, 1.0), 6) if updates or mins else None
    vwap = feats.get("vwap_distance_pct")
    if vwap is None:
        vwap = feats.get("price_vs_vwap")
    return {
        "spread_bps": feats.get("spread"),
        "update_count": feats.get("update_count_before_entry"),
        "volume_percentile": feats.get("rolling_volume_percentile"),
        "adx14": feats.get("adx14"),
        "rsi14": feats.get("rsi14"),
        "vwap_distance": vwap,
        "minutes_from_open": feats.get("minutes_from_open"),
        "day_high_update_speed": speed,
        "rolling_range_pct": feats.get("rolling_range_pct"),
        "day_high_update_count": feats.get("day_high_update_count_before_entry"),
    }


def _day_return_rank_map(price_idx: Mapping, universe: Sequence[str], day: str) -> dict[str, int]:
    ranked = _day_return_rank(price_idx, universe, day)
    return {sym: i + 1 for i, (sym, _) in enumerate(ranked)}


def _filter_predicate(filter_id: str) -> Callable[[Mapping[str, Any]], bool]:
    def _spread_ok(f: Mapping[str, Any], thr: float = 50.0) -> bool:
        v = f.get("spread_bps")
        return v is not None and float(v) <= thr

    def _update_ok(f: Mapping[str, Any], thr: float = 5.0) -> bool:
        v = f.get("update_count")
        return v is not None and float(v) <= thr

    def _vol_ok(f: Mapping[str, Any], thr: float = 80.0) -> bool:
        v = f.get("volume_percentile")
        return v is not None and float(v) >= thr

    def _adx_ok(f: Mapping[str, Any], thr: float = 30.0) -> bool:
        v = f.get("adx14")
        return v is not None and float(v) <= thr

    def _rsi_ok(f: Mapping[str, Any], thr: float = 50.0) -> bool:
        v = f.get("rsi14")
        return v is not None and float(v) >= thr

    def _mins_ok(f: Mapping[str, Any], thr: float = 150.0) -> bool:
        v = f.get("minutes_from_open")
        return v is not None and float(v) <= thr

    preds: dict[str, Callable[[Mapping[str, Any]], bool]] = {
        "F1_spread50": lambda f: _spread_ok(f),
        "F2_update5": lambda f: _update_ok(f),
        "F3_volpct80": lambda f: _vol_ok(f),
        "F4_adx30": lambda f: _adx_ok(f),
        "F5_rsi50": lambda f: _rsi_ok(f),
        "F6_mins150": lambda f: _mins_ok(f),
        "F7_spread50_update5": lambda f: _spread_ok(f) and _update_ok(f),
        "F8_spread50_volpct80": lambda f: _spread_ok(f) and _vol_ok(f),
        "F9_update5_volpct80": lambda f: _update_ok(f) and _vol_ok(f),
        "F10_spread50_update5_volpct80": lambda f: _spread_ok(f) and _update_ok(f) and _vol_ok(f),
    }
    return preds[filter_id]


def _is_winner_candidate(
    trade: Mapping[str, Any],
    rank: Optional[int],
) -> bool:
    if rank is not None and rank <= 10:
        return True
    if _num(trade.get("mfe_pct")) > 1.0:
        return True
    return _num(trade.get("pnl_yen_100")) > 0


def _is_noise_candidate(
    trade: Mapping[str, Any],
    rank: Optional[int],
) -> bool:
    if rank is not None and rank <= 20:
        return False
    if _num(trade.get("mfe_pct")) > 0.5:
        return False
    if _num(trade.get("pnl_yen_100")) > 0:
        return False
    return True


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


def _enrich_trade_row(
    trade: Mapping[str, Any],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
    rank_map: Mapping[str, int],
) -> dict[str, Any]:
    feats = _extract_entry_features(trade, bar_cache=bar_cache, micro_lookup=micro_lookup)
    mapped = _feature_map(feats)
    sym = _sym_key(trade.get("symbol"))
    day = str(trade.get("day") or "")[:8]
    sym_t = f"{sym}.T"
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    cached = bar_cache.get((sym_t, day))
    if ent and cached:
        bars, _ = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            stats = _high_update_stats(bars, ei, ei)
            mapped["day_high_update_count"] = stats.get("day_high_update_count_before_entry")
    if mapped.get("day_high_update_count") is None:
        mapped["day_high_update_count"] = mapped.get("update_count")
    rank = rank_map.get(sym)
    return {
        "symbol": sym,
        "day": day,
        "entry_time": trade.get("entry_time"),
        "entry_price": trade.get("entry_price"),
        "mfe_pct": trade.get("mfe_pct"),
        "pnl_yen_100": trade.get("pnl_yen_100"),
        "day_return_rank": rank,
        "position_key": _position_key(trade),
        **mapped,
        "_raw_feats": dict(feats),
        "cohort": (
            "winner_candidate"
            if _is_winner_candidate(trade, rank)
            else "noise_candidate"
            if _is_noise_candidate(trade, rank)
            else "other"
        ),
    }


def _missed_winner_qualifies(row: Mapping[str, Any]) -> bool:
    rank = row.get("day_return_rank")
    if rank is not None and int(rank) <= 50:
        return True
    return _num(row.get("mfe_pct")) > 1.0


def _feature_compare_rows(
    winners: Sequence[Mapping[str, Any]],
    losers: Sequence[Mapping[str, Any]],
    *,
    winner_label: str = "winner_candidate",
    loser_label: str = "noise_candidate",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in NOISE_COMPARE_FEATURES:
        wv = [_num(r.get(feat)) for r in winners if r.get(feat) is not None]
        lv = [_num(r.get(feat)) for r in losers if r.get(feat) is not None]
        if len(wv) < 2 or len(lv) < 2:
            rows.extend(
                [
                    {
                        "cohort": winner_label,
                        "feature_id": feat,
                        "count": len(wv),
                        "median": round(statistics.median(wv), 6) if wv else None,
                        "p25": _percentile(wv, 25) if wv else None,
                        "p75": _percentile(wv, 75) if wv else None,
                        "effect_size": None,
                        "separation_score": None,
                    },
                    {
                        "cohort": loser_label,
                        "feature_id": feat,
                        "count": len(lv),
                        "median": round(statistics.median(lv), 6) if lv else None,
                        "p25": _percentile(lv, 25) if lv else None,
                        "p75": _percentile(lv, 75) if lv else None,
                        "effect_size": None,
                        "separation_score": None,
                    },
                ]
            )
            continue
        eff = _cohens_d(wv, lv)
        sep = _separation_score(wv, lv)
        rows.extend(
            [
                {
                    "cohort": winner_label,
                    "feature_id": feat,
                    "count": len(wv),
                    "median": round(statistics.median(wv), 6),
                    "p25": _percentile(wv, 25),
                    "p75": _percentile(wv, 75),
                    "effect_size": eff,
                    "separation_score": sep,
                },
                {
                    "cohort": loser_label,
                    "feature_id": feat,
                    "count": len(lv),
                    "median": round(statistics.median(lv), 6),
                    "p25": _percentile(lv, 25),
                    "p75": _percentile(lv, 75),
                    "effect_size": eff,
                    "separation_score": sep,
                },
            ]
        )
    return rows


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    from datetime import datetime

    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("exit_time") or t.get("entry_time") or ""))
        or datetime.min.replace(tzinfo=JST),
    )
    return [_num(t.get("pnl_yen_100")) for t in ordered]


def _strategy_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    total = round(sum(pnls), 2)
    chron = _chron_pnls(trades)
    return {
        "total_pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(chron) if chron else 0.0, 2),
        "trade_count": len(trades),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "avg_pnl_yen_100": round(total / len(pnls), 2) if pnls else 0.0,
        "stop_low_mfe_count": sum(1 for t in trades if _is_stop_low_mfe(t)),
    }


def _load_strategies(
    repo_root: Path,
    *,
    price_idx: Mapping,
    parallel: bool,
    workers: int,
) -> dict[str, list[dict[str, Any]]]:
    bar_cache, _ = _build_bar_cache(repo_root)
    replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(repo_root)
    universe = _universe_symbols(replay_pool)
    micro_lookup = _build_micro_lookup(replay_pool)

    baseline_state, _ = _run_baseline_runtime(repo_root)
    trade_by_key = {_position_key(t): t for t in replay_pool}
    baseline_trades = _baseline_trade_rows(baseline_state, trade_by_key, price_idx)

    pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
    overlay_def = OVERLAY_DEFS["O_R003"]
    bar_cache, days = _build_bar_cache(repo_root)

    def _scan_day(day: str) -> list[dict[str, Any]]:
        return _scan_overlay_day(
            overlay_def,
            day=day,
            universe=universe,
            bar_cache=bar_cache,
            price_idx=price_idx,
        )

    overlay_by_day: dict[str, list[dict[str, Any]]] = {}
    if parallel:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_scan_day, day): day for day in days}
            for fut in as_completed(futs):
                day = futs[fut]
                overlay_by_day[day] = fut.result()
    else:
        for day in days:
            overlay_by_day[day] = _scan_day(day)

    overlay_all = [t for chunk in overlay_by_day.values() for t in chunk]
    overlay_g9 = [
        t
        for t in overlay_all
        if _passes_g9(_extract_entry_features(t, bar_cache=bar_cache, micro_lookup=micro_lookup))
    ]
    pbv2_g9 = [
        t
        for t in pbv2_candidates
        if _passes_g9(_extract_entry_features(t, bar_cache=bar_cache, micro_lookup=micro_lookup))
    ]

    variants = {
        OR_STRATEGY: (pbv2_candidates, overlay_all),
        OR_G9_STRATEGY: (pbv2_g9, overlay_g9),
    }

    out: dict[str, list[dict[str, Any]]] = {BASELINE_STRATEGY_ID: baseline_trades}
    for sid, (pbv2_part, overlay_part) in variants.items():
        merged = _merge_or_candidates(
            pbv2_part,
            overlay_part,
            bar_cache=bar_cache,
            overlay=overlay_def,
            guard_c_block=guard_c_block,
        )
        state = _simulate_precomputed_cap(merged, mode=f"phase531_{sid.lower()}")
        rows: list[dict[str, Any]] = []
        for r in _trade_rows_from_state(state, sid):
            pk = str(r.get("position_key") or "")
            src = trade_by_key.get(pk, {})
            mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=str(r.get("exit_time") or ""))
            rows.append({**dict(r), "strategy_id": sid, "mfe_pct": mfe, "mae_pct": mae})
        out[sid] = rows
    return out


def _run_day_enrich_job(
    day: str,
    candidate: str,
    *,
    or_trades: Sequence[Mapping[str, Any]],
    missed_keys: set[tuple[str, str]],
    bar_cache: Mapping,
    micro_lookup: Mapping,
    rank_map: Mapping[str, int],
) -> list[dict[str, Any]]:
    day_trades = [t for t in or_trades if str(t.get("day") or "")[:8] == day]
    rows: list[dict[str, Any]] = []
    for trade in day_trades:
        row = _enrich_trade_row(
            trade,
            bar_cache=bar_cache,
            micro_lookup=micro_lookup,
            rank_map=rank_map,
        )
        key = (row["symbol"], day)
        if candidate == "missed" and key in missed_keys and _missed_winner_qualifies(row):
            rows.append({k: row.get(k) for k in MISSED_WINNER_FIELDS})
        elif candidate == "or_cohort":
            rows.append(row)
    return rows


def _run_day_filter_job(
    day: str,
    filter_id: str,
    *,
    or_trades: Sequence[Mapping[str, Any]],
    feat_by_key: Mapping[str, Mapping[str, Any]],
    missed_keys: set[tuple[str, str]],
    noise_keys: set[str],
    baseline_pnl: float,
) -> dict[str, Any]:
    day_trades = [t for t in or_trades if str(t.get("day") or "")[:8] == day]
    pred = _filter_predicate(filter_id)
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for trade in day_trades:
        key = _position_key(trade)
        feats = feat_by_key.get(key, {})
        if pred(feats):
            accepted.append(trade)
        else:
            blocked.append(trade)

    met = _strategy_metrics(accepted)
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    prevented = round(sum(-p for p in blocked_pnls if p < 0), 2)
    lost = round(sum(p for p in blocked_pnls if p > 0), 2)

    missed_captured = sum(
        1
        for t in accepted
        if (_sym_key(t.get("symbol")), day) in missed_keys
    )
    missed_blocked = sum(
        1
        for t in blocked
        if (_sym_key(t.get("symbol")), day) in missed_keys
    )
    noise_blocked = sum(1 for t in blocked if _position_key(t) in noise_keys)
    noise_total = sum(1 for t in day_trades if _position_key(t) in noise_keys)

    return {
        "day": day,
        "filter_id": filter_id,
        **met,
        "pbv2_missed_winner_capture_count": missed_captured,
        "lost_winner_count": missed_blocked,
        "prevented_noise_count": noise_blocked,
        "noise_entry_count": noise_total,
        "net_improvement": round(met["total_pnl_yen_100"] - baseline_pnl, 2),
        "prevented_loss": prevented,
        "lost_profit": lost,
        "_accepted": accepted,
        "_blocked": blocked,
    }


def _aggregate_filter_rows(
    details: Sequence[Mapping[str, Any]],
    *,
    or_baseline: Mapping[str, Any],
    missed_keys: set[tuple[str, str]],
    noise_keys: set[str],
    capture_detail: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_filter: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in details:
        by_filter[str(row.get("filter_id") or "")].append(row)

    base_pnl = _num(or_baseline.get("total_pnl_yen_100"))
    base_slm = int(or_baseline.get("stop_low_mfe_count") or 0)
    base_missed = len(missed_keys)
    base_noise = len(noise_keys)
    base_score = _winner_capture_score(capture_detail, OR_STRATEGY)

    summaries: list[dict[str, Any]] = []
    for fid in FILTER_IDS:
        rows = by_filter.get(fid, [])
        if not rows:
            continue
        all_accepted: list[dict[str, Any]] = []
        for r in rows:
            all_accepted.extend(list(r.get("_accepted") or []))
        met = _strategy_metrics(all_accepted)
        missed_captured_keys = {
            (_sym_key(t.get("symbol")), str(t.get("day") or "")[:8])
            for t in all_accepted
            if (_sym_key(t.get("symbol")), str(t.get("day") or "")[:8]) in missed_keys
        }
        missed_cap = len(missed_captured_keys)
        lost_win = base_missed - missed_cap
        prev_noise = sum(int(r.get("prevented_noise_count") or 0) for r in rows)
        slm = met["stop_low_mfe_count"]
        summaries.append(
            {
                "filter_id": fid,
                **met,
                "winner_capture_score": base_score,
                "pbv2_missed_winner_capture_count": missed_cap,
                "unique_winner_retention_rate": round(missed_cap / base_missed, 4) if base_missed else 0.0,
                "noise_entry_reduction": round(prev_noise / base_noise, 4) if base_noise else 0.0,
                "stop_low_mfe_reduction": base_slm - slm,
                "lost_winner_count": lost_win,
                "prevented_noise_count": prev_noise,
                "net_improvement": round(met["total_pnl_yen_100"] - base_pnl, 2),
            }
        )
    return summaries


def _g9_capture_row(
    *,
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    capture_detail: Sequence[Mapping[str, Any]],
    missed_keys: set[tuple[str, str]],
    bar_cache: Mapping,
    micro_lookup: Mapping,
    price_idx: Mapping,
    universe: Sequence[str],
) -> dict[str, Any]:
    met = _strategy_metrics(trades)
    noise_n = 0
    for t in trades:
        day = str(t.get("day") or "")[:8]
        rank_map = _day_return_rank_map(price_idx, universe, day)
        row = _enrich_trade_row(t, bar_cache=bar_cache, micro_lookup=micro_lookup, rank_map=rank_map)
        if row.get("cohort") == "noise_candidate":
            noise_n += 1
    missed_cap = sum(
        1
        for t in trades
        if (_sym_key(t.get("symbol")), str(t.get("day") or "")[:8]) in missed_keys
    )
    return {
        "strategy_id": strategy_id,
        "day_return_top10_capture_rate": _avg_capture(
            capture_detail, strategy_id=strategy_id, universe_type="day_return", top_n=10, field="capture_rate"
        ),
        "day_return_top20_capture_rate": _avg_capture(
            capture_detail, strategy_id=strategy_id, universe_type="day_return", top_n=20, field="capture_rate"
        ),
        "mfe_gt1_capture_rate": _avg_capture(
            capture_detail, strategy_id=strategy_id, universe_type="day_return", top_n=10, field="effective_capture_rate"
        ),
        "mfe_gt3_capture_rate": _avg_capture(
            capture_detail, strategy_id=strategy_id, universe_type="day_return", top_n=10, field="strong_capture_rate"
        ),
        "pbv2_missed_winner_capture_count": missed_cap,
        "noise_entry_count": noise_n,
        **met,
        "winner_capture_score": _winner_capture_score(capture_detail, strategy_id),
    }


def _mandatory_answers(
    *,
    noise_compare: Sequence[Mapping[str, Any]],
    g9_rows: Sequence[Mapping[str, Any]],
    filter_rows: Sequence[Mapping[str, Any]],
    baseline_metrics: Mapping[str, Any],
    or_metrics: Mapping[str, Any],
    or_g9_metrics: Mapping[str, Any],
    missed_count: int,
) -> dict[str, Any]:
    sep_rows = [r for r in noise_compare if r.get("effect_size") is not None]
    best_feat = max(sep_rows, key=lambda r: abs(_float(r.get("effect_size"))), default={})
    max_sep = max(sep_rows, key=lambda r: abs(_float(r.get("separation_score"))), default={})
    best_feature = best_feat.get("feature_id") or max_sep.get("feature_id") or ""
    separable = abs(_float(best_feat.get("effect_size"))) >= 0.3 or abs(_float(max_sep.get("separation_score"))) >= 0.15

    or_g9 = next((r for r in g9_rows if r.get("strategy_id") == OR_G9_STRATEGY), {})
    or_row = next((r for r in g9_rows if r.get("strategy_id") == OR_STRATEGY), {})
    base_row = next((r for r in g9_rows if r.get("strategy_id") == BASELINE_STRATEGY_ID), {})

    or_cap = _float(or_row.get("day_return_top10_capture_rate"))
    g9_cap = _float(or_g9.get("day_return_top10_capture_rate"))
    discovery_retained = g9_cap >= or_cap * 0.85

    or_noise = int(or_row.get("noise_entry_count") or 0)
    g9_noise = int(or_g9.get("noise_entry_count") or 0)
    noise_reduced = g9_noise < or_noise

    g9_beats_base = _float(or_g9.get("total_pnl_yen_100")) > _float(base_row.get("total_pnl_yen_100"))
    g9_more_robust = (
        _float(or_g9.get("profit_factor")) >= _float(or_metrics.get("profit_factor"))
        and _float(or_g9.get("max_drawdown_yen_100")) <= _float(or_metrics.get("max_drawdown_yen_100"))
    )

    best_filter = max(filter_rows, key=lambda r: _float(r.get("net_improvement")), default={})
    best_filter_id = best_filter.get("filter_id") or ""
    worthy = (
        _float(best_filter.get("unique_winner_retention_rate")) >= 0.7
        and _float(best_filter.get("net_improvement")) > 0
    )

    proceed_runtime = worthy and discovery_retained and g9_beats_base
    prod_ok = False

    return {
        "1_missed_winner_noise_separable": separable,
        "2_best_separating_feature": best_feature,
        "2_best_effect_size": best_feat.get("effect_size"),
        "2_best_separation_score": max_sep.get("separation_score"),
        "3_g9_preserves_or_discovery": discovery_retained,
        "3_or_top10_capture": or_cap,
        "3_g9_top10_capture": g9_cap,
        "4_g9_reduces_noise": noise_reduced,
        "4_or_noise_count": or_noise,
        "4_g9_noise_count": g9_noise,
        "5_or_g9_beats_baseline": g9_beats_base,
        "6_or_g9_more_robust_than_or": g9_more_robust,
        "7_worthy_filter_for_pbv2": worthy,
        "8_best_filter": best_filter_id,
        "8_best_filter_net_improvement": best_filter.get("net_improvement"),
        "9_proceed_to_runtime_candidate": proceed_runtime,
        "10_production_adoption_ok": prod_ok,
        "missed_winner_count": missed_count,
    }


def _render_doc(result: Mapping[str, Any]) -> str:
    ans = result.get("mandatory_answers") or {}
    lines = [
        "# Phase531 — O_R003_OR Missed Winner Filter Study",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Includes 20260624:** {result.get('includes_20260624')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in sorted(ans.items()):
        lines.append(f"- **{k}:** {v}")
    lines.extend(
        [
            "",
            "## Strategies",
            "",
            "- BASELINE_RUNTIME (PBv2)",
            "- O_R003_OR",
            "- O_R003_OR + G9 (spread<=50bps, update_count<=5)",
            "",
            "Research only — no Runtime adoption.",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase531Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        period_end = _latest_live_day(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=period_end)
        bar_cache, days = _build_bar_cache(self.repo_root)
        days = [d for d in days if d >= PERIOD_START and d <= period_end]
        replay_pool, _, _ = _prepare_runtime_env(self.repo_root)
        universe = _universe_symbols(_filter_period(replay_pool, start=PERIOD_START, end=period_end))
        micro_lookup = _build_micro_lookup(replay_pool)

        trades_by_strategy = _load_strategies(
            self.repo_root, price_idx=price_idx, parallel=self.parallel, workers=workers
        )
        baseline_trades = trades_by_strategy.get(BASELINE_STRATEGY_ID, [])
        or_trades = trades_by_strategy.get(OR_STRATEGY, [])

        missed_keys = _b_or_only_keys(
            days=days,
            price_idx=price_idx,
            universe=universe,
            baseline_trades=baseline_trades,
            or_trades=or_trades,
        )

        enrich_jobs = [(day, cand) for day in days for cand in ("missed", "or_cohort")]
        missed_rows: list[dict[str, Any]] = []
        enriched_all: list[dict[str, Any]] = []

        def _enrich_job(day: str, cand: str) -> list[dict[str, Any]]:
            rank_map = _day_return_rank_map(price_idx, universe, day)
            return _run_day_enrich_job(
                day,
                cand,
                or_trades=or_trades,
                missed_keys=missed_keys,
                bar_cache=bar_cache,
                micro_lookup=micro_lookup,
                rank_map=rank_map,
            )

        if self.parallel and enrich_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_enrich_job, day, cand): (day, cand) for day, cand in enrich_jobs}
                for fut in as_completed(futs):
                    day, cand = futs[fut]
                    chunk = fut.result()
                    if cand == "missed":
                        missed_rows.extend(chunk)
                    else:
                        enriched_all.extend(chunk)
        else:
            for day, cand in enrich_jobs:
                chunk = _enrich_job(day, cand)
                if cand == "missed":
                    missed_rows.extend(chunk)
                else:
                    enriched_all.extend(chunk)

        winners = [r for r in enriched_all if r.get("cohort") == "winner_candidate"]
        noises = [r for r in enriched_all if r.get("cohort") == "noise_candidate"]
        noise_compare = _feature_compare_rows(winners, noises)

        feat_by_key = {str(r.get("position_key") or ""): r for r in enriched_all}
        noise_keys = {str(r.get("position_key") or "") for r in noises}

        capture_jobs = [(day, sid) for day in days for sid in STRATEGIES]
        capture_detail: list[dict[str, Any]] = []

        def _cap_job(day: str, sid: str) -> list[dict[str, Any]]:
            return _run_capture_day_job(
                day,
                sid,
                trades_by_strategy.get(sid, []),
                price_idx=price_idx,
                bar_cache=bar_cache,
                universe=universe,
            )

        if self.parallel and capture_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_cap_job, day, sid): (day, sid) for day, sid in capture_jobs}
                for fut in as_completed(futs):
                    capture_detail.extend(fut.result())
        else:
            for day, sid in capture_jobs:
                capture_detail.extend(_cap_job(day, sid))

        g9_capture_rows = [
            _g9_capture_row(
                strategy_id=sid,
                trades=trades_by_strategy.get(sid, []),
                capture_detail=capture_detail,
                missed_keys=missed_keys,
                bar_cache=bar_cache,
                micro_lookup=micro_lookup,
                price_idx=price_idx,
                universe=universe,
            )
            for sid in STRATEGIES
        ]

        or_baseline_met = _strategy_metrics(or_trades)
        baseline_day_pnl: dict[str, float] = defaultdict(float)
        for t in or_trades:
            baseline_day_pnl[str(t.get("day") or "")[:8]] += _num(t.get("pnl_yen_100"))

        filter_jobs = [(day, fid) for day in days for fid in FILTER_IDS]
        filter_details: list[dict[str, Any]] = []

        def _filt_job(day: str, fid: str) -> dict[str, Any]:
            return _run_day_filter_job(
                day,
                fid,
                or_trades=or_trades,
                feat_by_key=feat_by_key,
                missed_keys=missed_keys,
                noise_keys=noise_keys,
                baseline_pnl=baseline_day_pnl.get(day, 0.0),
            )

        if self.parallel and filter_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_filt_job, day, fid): (day, fid) for day, fid in filter_jobs}
                for fut in as_completed(futs):
                    filter_details.append(fut.result())
        else:
            for day, fid in filter_jobs:
                filter_details.append(_filt_job(day, fid))

        filter_summary = _aggregate_filter_rows(
            filter_details,
            or_baseline=or_baseline_met,
            missed_keys=missed_keys,
            noise_keys=noise_keys,
            capture_detail=capture_detail,
        )

        baseline_metrics = _strategy_metrics(baseline_trades)
        or_g9_metrics = _strategy_metrics(trades_by_strategy.get(OR_G9_STRATEGY, []))

        mandatory = _mandatory_answers(
            noise_compare=noise_compare,
            g9_rows=g9_capture_rows,
            filter_rows=filter_summary,
            baseline_metrics=baseline_metrics,
            or_metrics=or_baseline_met,
            or_g9_metrics=or_g9_metrics,
            missed_count=len(missed_keys),
        )

        return {
            "verdict": PHASE531_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": period_end,
            "includes_20260624": "20260624" in days,
            "parallel_workers": workers,
            "days_count": len(days),
            "missed_winner_features": missed_rows,
            "or_noise_features": noise_compare,
            "or_g9_capture": g9_capture_rows,
            "filter_summary": filter_summary,
            "mandatory_answers": mandatory,
            "missed_winner_count": len(missed_rows),
            "b_or_only_key_count": len(missed_keys),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "missed": reports / "phase531_missed_winner_features.csv",
            "noise": reports / "phase531_or_noise_features.csv",
            "g9": reports / "phase531_or_g9_capture.csv",
            "filters": reports / "phase531_filter_summary.csv",
            "report": reports / "phase531_report.json",
            "docs": kabu / "docs" / "operations" / "phase531_o_r003_or_missed_winner_filter_study.md",
        }
        _write_csv(paths["missed"], MISSED_WINNER_FIELDS, list(result.get("missed_winner_features") or []))
        _write_csv(paths["noise"], NOISE_FEATURE_FIELDS, list(result.get("or_noise_features") or []))
        _write_csv(paths["g9"], OR_G9_CAPTURE_FIELDS, list(result.get("or_g9_capture") or []))
        _write_csv(paths["filters"], FILTER_SUMMARY_FIELDS, list(result.get("filter_summary") or []))
        report_body = {k: v for k, v in result.items() if k not in ("missed_winner_features",)}
        paths["report"].write_text(json.dumps(report_body, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["docs"].parent.mkdir(parents=True, exist_ok=True)
        paths["docs"].write_text(_render_doc(result), encoding="utf-8")
        return paths
