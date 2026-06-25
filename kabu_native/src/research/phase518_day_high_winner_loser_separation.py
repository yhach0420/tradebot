"""
Phase518 — day_high overlay_only Winner / Loser separation study (research only).

Tests whether O_R003_OR overlay_only trades can be separated at ENTRY using
entry-time features only. No adoption. No production changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_indicators import Bar1m, BarIndicatorRow
from research.phase507_classic_strategy_battle import _run_baseline_runtime, _universe_symbols
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase515b_day_high_breakout_dependency_audit import (
    _bar_index_at,
    _classify_timing,
    _high_update_stats,
    _session_open_ts,
)
from research.phase515c_day_high_breakout_refinement import _entry_context
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase517_o_r003_or_robustness_audit import (
    _executed_trade_rows,
    _simulate_or_audited,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE518_VERDICT = "phase518_day_high_winner_loser_separation_done"
MAX_WORKERS_CAP = 4
FOCUS_SCENARIO = "O_R003_OR"

FEATURE_IDS: tuple[str, ...] = (
    "update_count_before_entry",
    "volume_ratio",
    "vwap_distance_pct",
    "adx14",
    "rsi14",
    "stoch_k",
    "stoch_d",
    "minutes_from_open",
    "price_vs_vwap",
    "price_vs_ema20",
    "board_imbalance",
    "spread",
    "rolling_range_pct",
    "rolling_volume_percentile",
)

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
    "mutual_information",
    "winner_missing_rate",
    "loser_missing_rate",
]

TRADE_COMPARE_FIELDS = [
    "scenario_id",
    "position_key",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "outcome",
    "breakout_type",
    *FEATURE_IDS,
]

BREAKOUT_TYPE_FIELDS = [
    "breakout_type",
    "trade_count",
    "win_count",
    "loss_count",
    "win_rate",
    "total_pnl_yen_100",
    "avg_pnl_yen_100",
    "median_update_count",
    "median_minutes_from_open",
    "median_vwap_distance_pct",
    "median_adx14",
    "median_volume_ratio",
    "median_board_imbalance",
]

EFFECT_RANK_FIELDS = [
    "rank",
    "feature_id",
    "effect_size",
    "separation_score",
    "mutual_information",
    "winner_median",
    "loser_median",
    "abs_effect_size",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _f(v: Optional[float]) -> float:
    return float(v) if v is not None else float("nan")


def _percentile(vals: Sequence[float], pct: float) -> Optional[float]:
    xs = sorted(vals)
    if not xs:
        return None
    idx = min(len(xs) - 1, int(round((pct / 100.0) * (len(xs) - 1))))
    return round(xs[idx], 6)


def _separation_score(winners: Sequence[float], losers: Sequence[float]) -> float:
    if not winners or not losers:
        return 0.0
    w_med = statistics.median(winners)
    l_med = statistics.median(losers)
    if w_med >= l_med:
        thr = l_med
        w_cov = sum(1 for x in winners if x >= thr) / len(winners)
        l_fp = sum(1 for x in losers if x >= thr) / len(losers)
    else:
        thr = w_med
        w_cov = sum(1 for x in winners if x <= thr) / len(winners)
        l_fp = sum(1 for x in losers if x <= thr) / len(losers)
    return round(w_cov - l_fp, 4)


def _build_micro_lookup(replay_pool: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[tuple[datetime, float, float]]]:
    out: dict[tuple[str, str], list[tuple[datetime, float, float]]] = defaultdict(list)
    for tr in replay_pool:
        sym = str(tr.get("symbol") or "").replace(".T", "")
        day = str(tr.get("day") or "")[:8]
        ent = _parse_ts(str(tr.get("entry_time") or ""))
        if not sym or not day or ent is None:
            continue
        board = tr.get("board_imbalance")
        if board is None:
            board = tr.get("entry_order_book_imbalance")
        spread = tr.get("spread_bps")
        if board is None and spread is None:
            continue
        out[(sym, day)].append(
            (
                ent,
                _float(board) if board is not None else float("nan"),
                _float(spread) if spread is not None else float("nan"),
            )
        )
    for key in out:
        out[key].sort(key=lambda x: x[0])
    return out


def _lookup_micro(
    lookup: Mapping[tuple[str, str], list[tuple[datetime, float, float]]],
    *,
    symbol: str,
    day: str,
    entry_time: datetime,
) -> tuple[Optional[float], Optional[float]]:
    rows = lookup.get((symbol.replace(".T", ""), day[:8]), [])
    if not rows:
        return None, None
    best: Optional[tuple[datetime, float, float]] = None
    best_delta = timedelta(days=999)
    for row in rows:
        delta = abs(row[0] - entry_time)
        if delta < best_delta:
            best_delta = delta
            best = row
    if best is None or best_delta > timedelta(minutes=2):
        return None, None
    board = best[1] if best[1] == best[1] else None
    spread = best[2] if best[2] == best[2] else None
    return board, spread


def _rolling_range_pct(bars: Sequence[Bar1m], i: int, window: int = 20) -> Optional[float]:
    start = max(0, i - window + 1)
    slice_b = bars[start : i + 1]
    if not slice_b:
        return None
    hi = max(b.high for b in slice_b)
    lo = min(b.low for b in slice_b)
    close = bars[i].close
    if close <= 0:
        return None
    return round((hi - lo) / close * 100.0, 4)


def _rolling_volume_percentile(bars: Sequence[Bar1m], i: int, window: int = 20) -> Optional[float]:
    start = max(0, i - window + 1)
    vols = [b.volume for b in bars[start : i + 1]]
    if not vols:
        return None
    cur = bars[i].volume
    rank = sum(1 for v in vols if v <= cur)
    return round(rank / len(vols) * 100.0, 2)


def _extract_entry_features(
    trade: Mapping[str, Any],
    *,
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    micro_lookup: Mapping[tuple[str, str], list[tuple[datetime, float, float]]],
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "").replace(".T", "")
    sym_t = f"{sym}.T"
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    ex = _parse_ts(str(trade.get("exit_time") or ""))
    cached = bar_cache.get((sym_t, day))
    out: dict[str, Any] = {fid: None for fid in FEATURE_IDS}
    if ent is None or not cached:
        return out
    bars, ind_rows = cached
    ei = _bar_index_at(bars, ent)
    if ei is None:
        return out
    ctx = _entry_context(bars, ind_rows, ei)
    ind = ind_rows[ei].values
    bar = bars[ei]
    vwap = _f(ind.get("VWAP"))
    ema20 = _f(ind.get("EMA20"))
    ent_px = bar.close
    open_ts = _session_open_ts(day)
    mins_open = round((ent - open_ts).total_seconds() / 60.0, 2)
    vwap_dist = _float(ctx.get("vwap_dist_pct"))
    price_vs_vwap = round((ent_px - vwap) / vwap * 100.0, 4) if vwap == vwap and vwap > 0 else None
    price_vs_ema20 = round((ent_px - ema20) / ema20 * 100.0, 4) if ema20 == ema20 and ema20 > 0 else None
    board, spread = _lookup_micro(micro_lookup, symbol=sym, day=day, entry_time=ent)
    if spread is None:
        spread = round((bar.high - bar.low) / ent_px * 10000.0, 2) if ent_px > 0 else None

    xi = _bar_index_at(bars, ex) if ex else ei
    stats = _high_update_stats(bars, ei, xi or ei)
    late = mins_open > 180 or int(stats.get("day_high_update_count_before_entry") or 0) >= 5
    timing_row = {
        "minutes_from_open": mins_open,
        "entry_is_late_breakout": late,
        **stats,
    }
    breakout_type = _classify_timing(timing_row)

    out.update(
        {
            "update_count_before_entry": int(ctx.get("updates_before") or 0),
            "volume_ratio": _float(ctx.get("vol_ratio")),
            "vwap_distance_pct": vwap_dist if vwap_dist < 900 else None,
            "adx14": ind.get("ADX"),
            "rsi14": ind.get("RSI14"),
            "stoch_k": ind.get("STOCH_K"),
            "stoch_d": ind.get("STOCH_D"),
            "minutes_from_open": mins_open,
            "price_vs_vwap": price_vs_vwap,
            "price_vs_ema20": price_vs_ema20,
            "board_imbalance": board,
            "spread": spread,
            "rolling_range_pct": _rolling_range_pct(bars, ei),
            "rolling_volume_percentile": _rolling_volume_percentile(bars, ei),
            "breakout_type": breakout_type,
        }
    )
    return out


def _enrich_trades_for_day(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    day: str,
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    micro_lookup: Mapping[tuple[str, str], list[tuple[datetime, float, float]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        if str(t.get("day") or "")[:8] != day:
            continue
        feats = _extract_entry_features(t, bar_cache=bar_cache, micro_lookup=micro_lookup)
        pnl = _float(t.get("pnl_yen_100"))
        rows.append(
            {
                "scenario_id": scenario_id,
                "position_key": _position_key(t),
                "symbol": str(t.get("symbol") or "").replace(".T", ""),
                "day": day,
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "pnl_yen_100": pnl,
                "outcome": "winner" if pnl > 0 else "loser",
                **{fid: feats.get(fid) for fid in FEATURE_IDS},
                "breakout_type": feats.get("breakout_type"),
            }
        )
    return rows


def _feature_separation_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    winners = [r for r in enriched if r.get("outcome") == "winner"]
    losers = [r for r in enriched if r.get("outcome") == "loser"]
    rows: list[dict[str, Any]] = []
    for feat in FEATURE_IDS:
        wv = [_float(r.get(feat)) for r in winners if r.get(feat) is not None]
        lv = [_float(r.get(feat)) for r in losers if r.get(feat) is not None]
        w_missing = 1.0 - (len(wv) / len(winners)) if winners else 0.0
        l_missing = 1.0 - (len(lv) / len(losers)) if losers else 0.0
        if len(wv) < 3 or len(lv) < 3:
            rows.append(
                {
                    "feature_id": feat,
                    "winner_count": len(wv),
                    "loser_count": len(lv),
                    "winner_median": None,
                    "loser_median": None,
                    "winner_p25": None,
                    "winner_p75": None,
                    "loser_p25": None,
                    "loser_p75": None,
                    "effect_size": None,
                    "separation_score": None,
                    "mutual_information": None,
                    "winner_missing_rate": round(w_missing, 4),
                    "loser_missing_rate": round(l_missing, 4),
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
                "mutual_information": _mi_median_split(wv, lv),
                "winner_missing_rate": round(w_missing, 4),
                "loser_missing_rate": round(l_missing, 4),
            }
        )
    return rows


def _breakout_type_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in enriched:
        by_type[str(r.get("breakout_type") or "unknown")].append(dict(r))
    rows: list[dict[str, Any]] = []
    for btype in ("true_breakout", "late_breakout", "high_chase", "noise", "unknown"):
        items = by_type.get(btype, [])
        if not items:
            continue
        pnls = [_float(r.get("pnl_yen_100")) for r in items]
        wins = sum(1 for p in pnls if p > 0)

        def _med(feat: str) -> Optional[float]:
            vals = [_float(r.get(feat)) for r in items if r.get(feat) is not None]
            return round(statistics.median(vals), 4) if vals else None

        rows.append(
            {
                "breakout_type": btype,
                "trade_count": len(items),
                "win_count": wins,
                "loss_count": len(items) - wins,
                "win_rate": round(wins / len(items), 4),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "avg_pnl_yen_100": round(sum(pnls) / len(items), 2),
                "median_update_count": _med("update_count_before_entry"),
                "median_minutes_from_open": _med("minutes_from_open"),
                "median_vwap_distance_pct": _med("vwap_distance_pct"),
                "median_adx14": _med("adx14"),
                "median_volume_ratio": _med("volume_ratio"),
                "median_board_imbalance": _med("board_imbalance"),
            }
        )
    return rows


def _effect_ranking(sep_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        [r for r in sep_rows if r.get("effect_size") is not None],
        key=lambda r: abs(float(r.get("effect_size") or 0)),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for i, r in enumerate(ranked, start=1):
        out.append(
            {
                "rank": i,
                "feature_id": r.get("feature_id"),
                "effect_size": r.get("effect_size"),
                "separation_score": r.get("separation_score"),
                "mutual_information": r.get("mutual_information"),
                "winner_median": r.get("winner_median"),
                "loser_median": r.get("loser_median"),
                "abs_effect_size": round(abs(float(r.get("effect_size") or 0)), 6),
            }
        )
    return out


def _rule_eval(
    enriched: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    predicate: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    winners = [r for r in enriched if r.get("outcome") == "winner"]
    losers = [r for r in enriched if r.get("outcome") == "loser"]
    w_keep = sum(1 for r in winners if predicate(r)) / len(winners) if winners else 0.0
    l_drop = sum(1 for r in losers if not predicate(r)) / len(losers) if losers else 0.0
    kept = [r for r in enriched if predicate(r)]
    kept_pnl = round(sum(_float(r.get("pnl_yen_100")) for r in kept), 2)
    return {
        "rule_id": rule_id,
        "winner_keep_rate": round(w_keep, 4),
        "loser_filter_rate": round(l_drop, 4),
        "kept_trades": len(kept),
        "kept_pnl_yen_100": kept_pnl,
    }


def _mandatory_answers(
    *,
    sep_rows: Sequence[Mapping[str, Any]],
    effect_rank: Sequence[Mapping[str, Any]],
    breakout_rows: Sequence[Mapping[str, Any]],
    enriched: Sequence[Mapping[str, Any]],
    rule_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    top = effect_rank[0] if effect_rank else {}
    top_feat = str(top.get("feature_id") or "")

    def _feat_row(fid: str) -> dict[str, Any]:
        return next((r for r in sep_rows if r.get("feature_id") == fid), {})

    upd = _feat_row("update_count_before_entry")
    adx = _feat_row("adx14")
    vwap = _feat_row("vwap_distance_pct")
    board = _feat_row("board_imbalance")
    vol = _feat_row("volume_ratio")

    def _effective(fid: str) -> bool:
        row = _feat_row(fid)
        es = row.get("effect_size")
        sep = row.get("separation_score")
        return es is not None and abs(float(es)) >= 0.2 and abs(float(sep or 0)) >= 0.1

    bt = {r["breakout_type"]: r for r in breakout_rows}
    late_rule = next((r for r in rule_rows if r.get("rule_id") == "late_breakout_filter"), {})
    chase_rule = next((r for r in rule_rows if r.get("rule_id") == "high_chase_filter"), {})

    best_rules = sorted(rule_rows, key=lambda r: float(r.get("loser_filter_rate") or 0) * float(r.get("winner_keep_rate") or 0), reverse=True)
    simple_improve = bool(best_rules and float(best_rules[0].get("loser_filter_rate") or 0) >= 0.3 and float(best_rules[0].get("winner_keep_rate") or 0) >= 0.5)

    refinements: list[str] = []
    if _effective("update_count_before_entry"):
        refinements.append("updates<=5 entry filter (lower update_count_before_entry)")
    if _effective("minutes_from_open"):
        refinements.append("session-open window cap (minutes_from_open)")
    if _effective("vwap_distance_pct"):
        refinements.append("vwap_distance_pct cap")
    if _effective("adx14"):
        refinements.append("ADX minimum at entry")
    if _effective("volume_ratio"):
        refinements.append("volume_ratio minimum at entry")
    if not refinements:
        refinements.append("breakout_type gate (true_breakout vs late/high_chase)")

    return {
        "1_best_separating_feature": top_feat,
        "1_best_effect_size": top.get("effect_size"),
        "1_best_separation_score": top.get("separation_score"),
        "2_update_count_effective": _effective("update_count_before_entry"),
        "2_update_count_effect_size": upd.get("effect_size"),
        "3_adx_effective": _effective("adx14"),
        "3_adx_effect_size": adx.get("effect_size"),
        "4_vwap_distance_effective": _effective("vwap_distance_pct"),
        "4_vwap_distance_effect_size": vwap.get("effect_size"),
        "5_board_imbalance_effective": _effective("board_imbalance"),
        "5_board_imbalance_effect_size": board.get("effect_size"),
        "6_volume_ratio_effective": _effective("volume_ratio"),
        "6_volume_ratio_effect_size": vol.get("effect_size"),
        "7_true_breakout_profile": bt.get("true_breakout"),
        "8_late_breakout_profile": bt.get("late_breakout"),
        "9_high_chase_profile": bt.get("high_chase"),
        "10_late_breakout_excludable_at_entry": float(late_rule.get("loser_filter_rate") or 0) >= 0.4,
        "10_late_rule": late_rule,
        "11_high_chase_excludable_at_entry": float(chase_rule.get("loser_filter_rate") or 0) >= 0.3,
        "11_chase_rule": chase_rule,
        "12_simple_rule_improvement_possible": simple_improve,
        "12_best_simple_rules": best_rules[:3],
        "13_next_refinement_candidates": refinements,
    }


@dataclass
class Phase518Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = MAX_WORKERS_CAP

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)
        bar_cache, days = _build_bar_cache(self.repo_root)
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        universe = _universe_symbols(replay_pool)
        micro_lookup = _build_micro_lookup(replay_pool)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
        overlay = OVERLAY_DEFS["O_R003"]
        overlay_scan: list[dict[str, Any]] = []

        def _scan_day(day: str) -> list[dict[str, Any]]:
            return _scan_overlay_day(
                overlay,
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )

        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_day, day): day for day in days}
                for fut in as_completed(futs):
                    overlay_scan.extend(fut.result())
        else:
            for day in days:
                overlay_scan.extend(_scan_day(day))

        merged = _merge_or_candidates(
            pbv2_candidates,
            overlay_scan,
            bar_cache=bar_cache,
            overlay=overlay,
            guard_c_block=guard_c_block,
        )
        or_result = _simulate_or_audited(merged, mode="phase518_o_r003_or")
        or_trades = _executed_trade_rows(or_result.state, FOCUS_SCENARIO)
        baseline_trades = _executed_trade_rows(baseline_state, "BASELINE")
        overlay_only = [t for t in or_trades if t.get("accepted_by_overlay") and not t.get("accepted_by_pbv2")]

        trade_buckets: dict[str, list[dict[str, Any]]] = {
            "overlay_only": overlay_only,
            "O_R003_OR": or_trades,
            "BASELINE": baseline_trades,
        }

        enriched_all: list[dict[str, Any]] = []
        jobs = [(sid, day) for sid in trade_buckets for day in days]

        def _job(sid: str, day: str) -> list[dict[str, Any]]:
            return _enrich_trades_for_day(
                trade_buckets[sid],
                scenario_id=sid,
                day=day,
                bar_cache=bar_cache,
                micro_lookup=micro_lookup,
            )

        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_job, sid, day): (sid, day) for sid, day in jobs}
                for fut in as_completed(futs):
                    enriched_all.extend(fut.result())
        else:
            for sid, day in jobs:
                enriched_all.extend(_job(sid, day))

        overlay_enriched = [r for r in enriched_all if r.get("scenario_id") == "overlay_only"]
        sep_rows = _feature_separation_rows(overlay_enriched)
        effect_rank = _effect_ranking(sep_rows)
        breakout_rows = _breakout_type_rows(overlay_enriched)

        rule_rows = [
            _rule_eval(
                overlay_enriched,
                rule_id="late_breakout_filter",
                predicate=lambda r: _float(r.get("minutes_from_open")) <= 180
                and _float(r.get("update_count_before_entry")) <= 5,
            ),
            _rule_eval(
                overlay_enriched,
                rule_id="high_chase_filter",
                predicate=lambda r: _float(r.get("vwap_distance_pct")) <= 5.0,
            ),
            _rule_eval(
                overlay_enriched,
                rule_id="adx15_filter",
                predicate=lambda r: _float(r.get("adx14")) >= 15.0,
            ),
            _rule_eval(
                overlay_enriched,
                rule_id="volume_ratio_filter",
                predicate=lambda r: _float(r.get("volume_ratio")) >= 1.0,
            ),
            _rule_eval(
                overlay_enriched,
                rule_id="early_session_filter",
                predicate=lambda r: _float(r.get("minutes_from_open")) <= 120.0,
            ),
        ]

        mandatory = _mandatory_answers(
            sep_rows=sep_rows,
            effect_rank=effect_rank,
            breakout_rows=breakout_rows,
            enriched=overlay_enriched,
            rule_rows=rule_rows,
        )

        summary_context = {
            "BASELINE": _strategy_metrics_safe(
                baseline_state,
                strategy_id="BASELINE",
                entry_rule_id="PBv2",
                exit_rule_id="RUNTIME",
            ),
            "O_R003_OR": _strategy_metrics_safe(
                or_result.state,
                strategy_id=FOCUS_SCENARIO,
                entry_rule_id="PBv2+OR",
                exit_rule_id="RUNTIME/PB",
            ),
            "overlay_only": _metrics_from_overlay(overlay_enriched),
        }

        return {
            "verdict": PHASE518_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "parallel_workers": workers,
            "overlay_only_trade_count": len(overlay_enriched),
            "winner_count": sum(1 for r in overlay_enriched if r.get("outcome") == "winner"),
            "loser_count": sum(1 for r in overlay_enriched if r.get("outcome") == "loser"),
            "summary_context": summary_context,
            "feature_separation_rows": sep_rows,
            "winner_loser_comparison_rows": enriched_all,
            "breakout_type_rows": breakout_rows,
            "effect_size_ranking_rows": effect_rank,
            "simple_rule_rows": rule_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "feature_separation": reports / "phase518_feature_separation.csv",
            "winner_loser": reports / "phase518_winner_loser_comparison.csv",
            "breakout_type": reports / "phase518_breakout_type_analysis.csv",
            "effect_ranking": reports / "phase518_effect_size_ranking.csv",
            "report": reports / "phase518_report.json",
            "docs": kabu / "docs" / "operations" / "phase518_day_high_winner_loser_separation.md",
        }
        _write_csv(paths["feature_separation"], FEATURE_SEP_FIELDS, list(result.get("feature_separation_rows") or []))
        _write_csv(paths["winner_loser"], TRADE_COMPARE_FIELDS, list(result.get("winner_loser_comparison_rows") or []))
        _write_csv(paths["breakout_type"], BREAKOUT_TYPE_FIELDS, list(result.get("breakout_type_rows") or []))
        _write_csv(paths["effect_ranking"], EFFECT_RANK_FIELDS, list(result.get("effect_size_ranking_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _metrics_from_overlay(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "trades": len(pnls),
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
    }


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase518 — day_high Winner / Loser Separation",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Overlay-only trades:** {result.get('overlay_only_trade_count')} "
        f"(W={result.get('winner_count')} L={result.get('loser_count')})",
        "",
        "## Effect size ranking (top 5)",
        "",
    ]
    for row in (result.get("effect_size_ranking_rows") or [])[:5]:
        lines.append(
            f"- **{row.get('feature_id')}**: d={row.get('effect_size')}, "
            f"sep={row.get('separation_score')}, W_med={row.get('winner_median')}, L_med={row.get('loser_median')}"
        )
    lines.extend(["", "## Breakout types", ""])
    for row in result.get("breakout_type_rows") or []:
        lines.append(
            f"- **{row.get('breakout_type')}**: n={row.get('trade_count')}, "
            f"win_rate={row.get('win_rate')}, PnL={row.get('total_pnl_yen_100')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for i in range(1, 14):
        key = next((k for k in ma if k.startswith(f"{i}_")), None)
        if key:
            lines.append(f"{i}. **{key}**: {ma.get(key)}")
    return "\n".join(lines) + "\n"
